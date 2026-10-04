"""s7 · 整体汇总（大模型）。

把逐题评分汇总成一份可指导下一场面试的报告。

**输入是摘要不是全文**：每道题只送题号、主题、分数、总评和要点。逐题分析里
已经包含了汇总所需的信息，再送一遍原始长文只会让成本翻几倍而几乎不增加信息量。

**这一步独有的价值是「跨题模式」**：单看每道题，你只能知道哪题答得好；
把十几道题放在一起，才能看出「这个人几乎每道题都只讲用了什么、不讲为什么选它」
这类**行为习惯**。提示词里专门强调了这个字段必须有跨题证据支持，不许硬凑。

**关于结果预判**：从一段转写推断录用结果，中间隔着面试官的主观偏好、岗位竞争、
HC 数量等大量看不到的变量。所以提示词要求模型给出校准过的判断并说明局限，
而不是一个斩钉截铁的结论 —— 过度自信的预测比不给更糟。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.errors import ConfigError, LlmError, LlmJsonError
from app.db.engine import get_engine
from app.db.models import (
    Interview,
    PipelineStage,
    QaAnalysis,
    QaPair,
    Report,
    Transcript,
)
from app.pipeline.stages.base import Stage, StageContext, StageError
from app.prompts import get as get_prompt
from app.services import llm_log
from app.services.llm_deepseek import DeepSeekClient

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 16000

# 送进提示词的摘要里，每道题最多保留几条要点
_MAX_ITEMS = 3
# 单条文本的截断长度
_MAX_ITEM_CHARS = 160


class SummaryStage(Stage):
    name = "s7_summary"
    label = "生成整体报告"

    async def compute_input_hash(self, ctx: StageContext) -> str:
        prompt = get_prompt("interview_summary")
        with Session(get_engine()) as session:
            pairs = session.exec(
                select(QaPair).where(QaPair.interview_id == ctx.interview_id).order_by(QaPair.seq)
            ).all()
            analyses = session.exec(
                select(QaAnalysis).where(QaAnalysis.interview_id == ctx.interview_id)
            ).all()

        if not analyses:
            raise StageError("找不到逐题评分，请先执行 s6_qa_scoring")

        # 逐题评分一旦变化，汇总就该重算
        fingerprint = json.dumps(
            sorted(
                [[a.qa_pair_id, a.overall_score, a.summary, a.model, a.prompt_version] for a in analyses]
            ),
            ensure_ascii=False,
            default=str,
        )
        settings = get_settings()
        return hashlib.sha256(
            f"{hashlib.sha256(fingerprint.encode()).hexdigest()}"
            f"|{prompt.version}|{settings.deepseek_model}|{len(pairs)}".encode()
        ).hexdigest()

    async def execute(self, ctx: StageContext) -> dict[str, Any]:
        prompt = get_prompt("interview_summary")
        settings = get_settings()

        with Session(get_engine()) as session:
            interview = session.get(Interview, ctx.interview_id)
            pairs = session.exec(
                select(QaPair)
                .where(QaPair.interview_id == ctx.interview_id)
                .order_by(QaPair.seq)
            ).all()
            analyses = session.exec(
                select(QaAnalysis).where(QaAnalysis.interview_id == ctx.interview_id)
            ).all()
            transcript = session.exec(
                select(Transcript).where(Transcript.interview_id == ctx.interview_id)
            ).first()

            # 标签可信度在 s4 的阶段产物里，作为文本可靠性的上下文传给模型
            role_stage = session.exec(
                select(PipelineStage)
                .where(PipelineStage.run_id == ctx.run_id)
                .where(PipelineStage.stage == "s4_role_mapping")
            ).first()

        if not analyses:
            raise StageError("找不到逐题评分，请先执行 s6_qa_scoring")

        pair_by_id = {p.id: p for p in pairs}
        scored = [(pair_by_id[a.qa_pair_id], a) for a in analyses if a.qa_pair_id in pair_by_id]
        scored.sort(key=lambda item: item[0].seq)

        if not scored:
            raise StageError("逐题评分与问答对无法对应")

        label_reliability = None
        if role_stage and role_stage.output_json:
            try:
                label_reliability = json.loads(role_stage.output_json).get("label_reliability")
            except (ValueError, AttributeError):
                pass

        overview = self._build_overview(scored, pairs, transcript, label_reliability)
        digest = "\n\n".join(self._format_pair(pair, analysis) for pair, analysis in scored)

        ctx.emit("正在生成整体报告…", 95)

        try:
            client = DeepSeekClient()
        except ConfigError as exc:
            raise StageError(str(exc)) from exc

        user_prompt = prompt.render(
            position_hint=(interview.position if interview and interview.position else "未提供"),
            pair_count=len(scored),
            overview=overview,
            pairs=digest,
        )

        try:
            data, result = await client.chat_json(
                prompt.system, user_prompt, thinking=True, max_tokens=MAX_OUTPUT_TOKENS
            )
        except (LlmError, LlmJsonError) as exc:
            raise StageError(f"整体汇总失败：{exc}", retryable=exc.retryable) from exc

        llm_log.record_call(
            stage=self.name,
            model=settings.deepseek_model,
            prompt_version=prompt.version,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_estimate=result.cost_estimate,
            latency_ms=result.latency_ms,
            interview_id=ctx.interview_id,
            run_id=ctx.run_id,
        )

        summary = self._normalize(data)
        version = self._persist(ctx, summary, settings.deepseek_model, prompt.version)

        logger.info(
            "整体报告已生成（第 %d 版）：总分 %.0f，能力项 %d 个，跨题模式 %d 条",
            version,
            summary["overall_score"],
            len(summary["competency_radar"]),
            len(summary["recurring_patterns"]),
        )
        ctx.emit(f"报告已生成，总分 {summary['overall_score']:.0f}", 100)

        return {
            "prompt_version": prompt.version,
            "report_version": version,
            "overall_score": summary["overall_score"],
            "competency_count": len(summary["competency_radar"]),
            "pattern_count": len(summary["recurring_patterns"]),
            "summary": summary,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "cost_estimate": result.cost_estimate,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _build_overview(
        scored: list[tuple[QaPair, QaAnalysis]],
        pairs: list[QaPair],
        transcript: Transcript | None,
        label_reliability: str | None,
    ) -> str:
        scores = [a.overall_score for _, a in scored]
        unscored = len(pairs) - len(scored)
        lines = [
            f"- 已评分 {len(scored)} 道题"
            + (f"，另有 {unscored} 道未评分（寒暄或没有实质回答）" if unscored else ""),
            f"- 逐题得分（十分制）：最高 {max(scores):.1f}，最低 {min(scores):.1f}，"
            f"平均 {sum(scores) / len(scores):.1f}",
        ]
        if transcript and transcript.duration_ms:
            lines.append(f"- 面试时长约 {transcript.duration_ms / 60000:.0f} 分钟")
        if label_reliability:
            lines.append(
                f"- 说话人标签可信度：{label_reliability}"
                + (
                    "（转写文本可能有个别句子的问答归属错位，评估时请以内容为准）"
                    if label_reliability in ("low", "medium")
                    else ""
                )
            )
        return "\n".join(lines)

    @staticmethod
    def _format_pair(pair: QaPair, analysis: QaAnalysis) -> str:
        parts = [f"### 第 {pair.seq} 题 · {pair.topic or '（无主题）'} · {analysis.overall_score}/10"]

        try:
            dims = json.loads(analysis.dimension_scores_json or "{}")
            if dims:
                parts.append(
                    "维度：" + " / ".join(f"{k} {v}" for k, v in dims.items())
                )
        except ValueError:
            pass

        if analysis.summary:
            parts.append(f"总评：{analysis.summary}")

        for label, raw in (
            ("优点", analysis.strengths_json),
            ("不足", analysis.weaknesses_json),
            ("知识点缺口", analysis.knowledge_points_json),
        ):
            items = SummaryStage._parse_items(raw)
            if items:
                parts.append(f"{label}：")
                parts.extend(f"  - {item}" for item in items)

        return "\n".join(parts)

    @staticmethod
    def _parse_items(raw: str) -> list[str]:
        try:
            value = json.loads(raw or "[]")
        except ValueError:
            return []
        if not isinstance(value, list):
            return []

        out: list[str] = []
        for item in value[:_MAX_ITEMS]:
            if isinstance(item, dict):
                text = item.get("title") or ""
                detail = item.get("detail") or ""
                text = f"{text}：{detail}" if detail else text
            else:
                text = str(item)
            text = text.strip()
            if text:
                out.append(text[:_MAX_ITEM_CHARS])
        return out

    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any]:
        """把模型返回的汇总规整成可入库的结构。

        模型偶尔会给越界的分数或错误的类型，这里统一钳到合法范围。
        """
        def clamp100(value: Any) -> float:
            try:
                score = float(value)
            except (TypeError, ValueError):
                return 0.0
            return round(max(0.0, min(score, 100.0)), 1)

        def str_list(value: Any, limit: int = 8) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(x).strip() for x in value if str(x).strip()][:limit]

        radar_raw = data.get("competency_radar")
        radar: dict[str, float] = {}
        if isinstance(radar_raw, dict):
            for name, score in list(radar_raw.items())[:8]:
                radar[str(name)[:40]] = clamp100(score)

        plan: list[dict[str, Any]] = []
        if isinstance(data.get("improvement_plan"), list):
            for entry in data["improvement_plan"][:6]:
                if isinstance(entry, dict):
                    plan.append(
                        {
                            "area": str(entry.get("area") or "").strip()[:60],
                            "actions": str_list(entry.get("actions"), limit=6),
                        }
                    )

        predicted_raw = data.get("predicted_result")
        predicted: dict[str, Any] = {}
        if isinstance(predicted_raw, dict):
            confidence = str(predicted_raw.get("confidence") or "").lower()
            if confidence not in ("high", "medium", "low"):
                confidence = "low"  # 无法解析时保守处理
            predicted = {
                "verdict": str(predicted_raw.get("verdict") or "").strip()[:120],
                "confidence": confidence,
                "reason": str(predicted_raw.get("reason") or "").strip()[:600],
            }

        return {
            "overall_score": clamp100(data.get("overall_score")),
            "assessment": str(data.get("assessment") or "").strip()[:2000],
            "competency_radar": radar,
            "top_strengths": str_list(data.get("top_strengths")),
            "critical_weaknesses": str_list(data.get("critical_weaknesses")),
            "recurring_patterns": str_list(data.get("recurring_patterns")),
            "improvement_plan": plan,
            "predicted_result": predicted,
        }

    def _persist(
        self, ctx: StageContext, summary: dict[str, Any], model: str, prompt_version: str
    ) -> int:
        """写入报告。保留历史版本，version 递增 —— 便于以后做历史对比。"""
        with Session(get_engine()) as session:
            existing = session.exec(
                select(Report).where(Report.interview_id == ctx.interview_id)
            ).all()
            version = max((r.version for r in existing), default=0) + 1

            session.add(
                Report(
                    interview_id=ctx.interview_id,
                    version=version,
                    overall_score=summary["overall_score"],
                    summary_json=json.dumps(summary, ensure_ascii=False),
                    model=model,
                    prompt_version=prompt_version,
                )
            )
            session.commit()

        return version
