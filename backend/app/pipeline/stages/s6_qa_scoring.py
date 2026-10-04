"""s6 · 逐题评分（大模型）。

对每个「问题-回答」对给出评分、改进建议、知识点补充和追问预测。

**成本控制**：只送当前这一道题的问答文本，不送全文。这是刻意的取舍 ——
全文能让模型更了解上下文，但会让每次调用贵一个数量级，而逐题评分并不需要
知道别的题目聊了什么。整体印象由 s7 汇总阶段负责。

**批级幂等**：分析结果按题存进 QaAnalysis。重跑时先查每道题是否已有
当前 prompt 版本的分析结果，只对缺失的那些发起调用。所以某一批失败后重跑，
不会重做已经成功的批次 —— 这一点和 s3 不同（s3 的整批重跑是已知取舍）。

**跳过两类题**：寒暄之类的 `is_off_topic` 题（评了也没意义），
以及没有回答内容的题。
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
from app.db.models import Interview, QaAnalysis, QaPair
from app.pipeline.stages.base import Stage, StageContext, StageError
from app.prompts import get as get_prompt
from app.services import llm_log
from app.services.llm_deepseek import DeepSeekClient

logger = logging.getLogger(__name__)

# 每次调用评几道题。输出里每道题有四个数组加知识点，体积不小，
# 给太大容易触发截断（s3 踩过这个坑）。
BATCH_SIZE = 6
MAX_OUTPUT_TOKENS = 16000

# 评分维度的取值上限，用于把模型给的分钳到合理范围
MAX_DIMENSION_SCORE = 10

_DIMENSIONS = ("技术准确性", "表达结构", "深度", "完整性")


class QaScoringStage(Stage):
    name = "s6_qa_scoring"
    label = "逐题评分"

    async def compute_input_hash(self, ctx: StageContext) -> str:
        prompt = get_prompt("qa_scoring")
        with Session(get_engine()) as session:
            pairs = session.exec(
                select(QaPair).where(QaPair.interview_id == ctx.interview_id)
            ).all()

        if not pairs:
            raise StageError("找不到问答对，请先执行 s5_qa_segmentation")

        # 题目的内容与顺序决定评分结果；prompt 版本或模型变了就该重评
        fingerprint = json.dumps(
            [
                [p.seq, p.question_text, p.answer_text, p.is_off_topic]
                for p in sorted(pairs, key=lambda x: x.seq)
            ],
            ensure_ascii=False,
        )
        settings = get_settings()
        return hashlib.sha256(
            f"{hashlib.sha256(fingerprint.encode()).hexdigest()}"
            f"|{prompt.version}|{settings.deepseek_model}".encode()
        ).hexdigest()

    async def execute(self, ctx: StageContext) -> dict[str, Any]:
        prompt = get_prompt("qa_scoring")
        settings = get_settings()
        model = settings.deepseek_model

        with Session(get_engine()) as session:
            interview = session.get(Interview, ctx.interview_id)
            all_pairs = session.exec(
                select(QaPair)
                .where(QaPair.interview_id == ctx.interview_id)
                .order_by(QaPair.seq)
            ).all()

            existing = session.exec(
                select(QaAnalysis).where(QaAnalysis.interview_id == ctx.interview_id)
            ).all()

        if not all_pairs:
            raise StageError("找不到问答对，请先执行 s5_qa_segmentation")

        # 批级幂等：已经有当前 prompt 版本结果的题不再重评
        done_seqs = {
            row.qa_pair_id
            for row in existing
            if row.prompt_version == prompt.version and row.model == model
        }
        analyzed_pair_ids = {row.qa_pair_id for row in existing}
        pair_by_id = {p.id: p for p in all_pairs}

        todo: list[QaPair] = []
        skipped: dict[str, int] = {"off_topic": 0, "empty_answer": 0, "cached": 0}

        for pair in all_pairs:
            if pair.is_off_topic:
                skipped["off_topic"] += 1
                continue
            if not (pair.answer_text or "").strip():
                skipped["empty_answer"] += 1
                continue
            if pair.id in done_seqs:
                skipped["cached"] += 1
                continue
            todo.append(pair)

        if not todo:
            logger.info("所有题目都已有评分结果，无需重评：%s", skipped)
            ctx.emit("评分结果已是最新", 99)
            return {
                "prompt_version": prompt.version,
                "scored": 0,
                "skipped": skipped,
                "batches": 0,
                "failed_batches": [],
                "reused": True,
            }

        # 已经分析过但 prompt 版本变了：删掉旧结果重建
        stale = [
            row for row in existing
            if row.qa_pair_id in pair_by_id
            and pair_by_id[row.qa_pair_id] in todo
            and row.qa_pair_id in analyzed_pair_ids
        ]
        if stale:
            self._delete_analyses([row.id for row in stale])

        batches = [todo[i : i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
        total_batches = len(batches)
        logger.info(
            "开始逐题评分：%d 题，分 %d 批（跳过 %s）", len(todo), total_batches, skipped
        )

        try:
            client = DeepSeekClient()
        except ConfigError as exc:
            raise StageError(str(exc)) from exc

        position_hint = interview.position if interview and interview.position else "未提供"

        scored = 0
        failed_batches: list[int] = []
        warnings: list[str] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for index, batch in enumerate(batches):
            if ctx.is_canceled():
                raise StageError("任务已取消")

            batch_no = index + 1
            ctx.emit(
                f"正在评分 第 {batch_no}/{total_batches} 批（{len(batch)} 题）…",
                94 + int(index / total_batches * 4),
            )

            try:
                analyses, usage = await self._score_batch(
                    client, prompt, batch, position_hint, warnings
                )
            except (LlmError, LlmJsonError) as exc:
                # 单批失败不影响其余批次；已成功的批次有落库，重跑时会被跳过
                logger.warning("第 %d 批评分失败：%s", batch_no, exc)
                failed_batches.append(batch_no)
                continue

            total_prompt_tokens += usage[0]
            total_completion_tokens += usage[1]
            scored += self._persist(batch, analyses, model, prompt.version)
            warnings.extend(
                f"第 {batch_no} 批：序号 {s} 没有返回分析结果"
                for s in {p.seq for p in batch} - {a.get("seq") for a in analyses}
            )

        cost = total_prompt_tokens / 1e6 * 0.30 + total_completion_tokens / 1e6 * 1.20
        llm_log.record_call(
            stage=self.name,
            model=model,
            prompt_version=prompt.version,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            cost_estimate=cost,
            latency_ms=0,
            interview_id=ctx.interview_id,
            run_id=ctx.run_id,
            status="ok" if not failed_batches else "partial",
        )

        logger.info(
            "逐题评分完成：%d 题已评分，失败批次 %s，警告 %d 条",
            scored,
            failed_batches or "无",
            len(warnings),
        )
        ctx.emit(f"完成 {scored} 道题的评分", 99)

        return {
            "prompt_version": prompt.version,
            "scored": scored,
            "skipped": skipped,
            "batches": total_batches,
            "failed_batches": failed_batches,
            "warnings": warnings,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "cost_estimate": cost,
        }

    # ------------------------------------------------------------------
    async def _score_batch(
        self,
        client: DeepSeekClient,
        prompt,
        batch: list[QaPair],
        position_hint: str,
        warnings: list[str],
    ) -> tuple[list[dict[str, Any]], tuple[int, int]]:
        blocks = [self._format_pair(p) for p in batch]
        user_prompt = prompt.render(
            position_hint=position_hint,
            pair_count=len(batch),
            pairs="\n\n".join(blocks),
        )

        # 思考模式开着：判断"这个回答是否准确、缺了什么"需要推理
        data, result = await client.chat_json(
            prompt.system, user_prompt, thinking=True, max_tokens=MAX_OUTPUT_TOKENS
        )

        raw = data.get("analyses")
        if not isinstance(raw, list):
            raise LlmJsonError("评分结果里 analyses 不是数组", detail=json.dumps(data)[:300])

        valid_seqs = {p.seq for p in batch}
        analyses: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                seq = int(item.get("seq"))
            except (TypeError, ValueError):
                continue
            if seq not in valid_seqs:
                warnings.append(f"评分结果含不存在的题号 {seq}，已忽略")
                continue
            analyses.append(self._normalize(item, seq))

        return analyses, (result.prompt_tokens, result.completion_tokens)

    @staticmethod
    def _format_pair(pair: QaPair) -> str:
        parts = [f"### 第 {pair.seq} 题"]
        if pair.topic:
            parts.append(f"主题：{pair.topic}")
        if pair.is_followup:
            parts.append("（这是对前面某题的追问）")
        parts.append(f"面试官问：\n{pair.question_text}")
        parts.append(f"候选人答：\n{pair.answer_text}")
        return "\n".join(parts)

    @staticmethod
    def _normalize(item: dict[str, Any], seq: int) -> dict[str, Any]:
        """把模型返回的一条分析规整成可入库的结构。

        模型偶尔会给越界的分数或错误的类型，这里全部钳到合法范围，
        而不是让一条脏数据把整批拖垮。
        """
        def clamp(value: Any) -> float:
            try:
                score = float(value)
            except (TypeError, ValueError):
                return 0.0
            return round(max(0.0, min(score, float(MAX_DIMENSION_SCORE))), 1)

        def str_list(value: Any, limit: int = 6) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(x).strip() for x in value if str(x).strip()][:limit]

        dims_raw = item.get("dimension_scores")
        dims: dict[str, float] = {}
        if isinstance(dims_raw, dict):
            for name in _DIMENSIONS:
                if name in dims_raw:
                    dims[name] = clamp(dims_raw[name])

        knowledge = []
        if isinstance(item.get("knowledge_points"), list):
            for point in item["knowledge_points"][:5]:
                if isinstance(point, dict):
                    knowledge.append(
                        {
                            "title": str(point.get("title") or "").strip()[:120],
                            "detail": str(point.get("detail") or "").strip()[:800],
                        }
                    )
                elif isinstance(point, str):
                    knowledge.append({"title": point.strip()[:120], "detail": ""})

        return {
            "seq": seq,
            "overall_score": clamp(item.get("overall_score")),
            "summary": str(item.get("summary") or "").strip()[:500],
            "dimension_scores": dims,
            "strengths": str_list(item.get("strengths")),
            "weaknesses": str_list(item.get("weaknesses")),
            "improvement": str_list(item.get("improvement")),
            "knowledge_points": knowledge,
            "predicted_followups": str_list(item.get("predicted_followups")),
        }

    def _delete_analyses(self, ids: list[str]) -> None:
        with Session(get_engine()) as session:
            for analysis_id in ids:
                row = session.get(QaAnalysis, analysis_id)
                if row is not None:
                    session.delete(row)
            session.commit()

    def _persist(
        self,
        batch: list[QaPair],
        analyses: list[dict[str, Any]],
        model: str,
        prompt_version: str,
    ) -> int:
        by_seq = {p.seq: p for p in batch}
        count = 0

        with Session(get_engine()) as session:
            for analysis in analyses:
                pair = by_seq.get(analysis["seq"])
                if pair is None:
                    continue

                # 同一道题可能有旧版本的结果，先清掉
                old = session.exec(
                    select(QaAnalysis).where(QaAnalysis.qa_pair_id == pair.id)
                ).all()
                for row in old:
                    session.delete(row)
                session.commit()

                session.add(
                    QaAnalysis(
                        qa_pair_id=pair.id,
                        interview_id=pair.interview_id,
                        overall_score=analysis["overall_score"],
                        summary=analysis["summary"],
                        dimension_scores_json=json.dumps(
                            analysis["dimension_scores"], ensure_ascii=False
                        ),
                        strengths_json=json.dumps(analysis["strengths"], ensure_ascii=False),
                        weaknesses_json=json.dumps(analysis["weaknesses"], ensure_ascii=False),
                        improvement_json=json.dumps(analysis["improvement"], ensure_ascii=False),
                        knowledge_points_json=json.dumps(
                            analysis["knowledge_points"], ensure_ascii=False
                        ),
                        predicted_followups_json=json.dumps(
                            analysis["predicted_followups"], ensure_ascii=False
                        ),
                        model=model,
                        prompt_version=prompt_version,
                        raw_response=json.dumps(analysis, ensure_ascii=False)[:20000],
                    )
                )
                count += 1
            session.commit()

        return count
