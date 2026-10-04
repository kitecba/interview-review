"""s5 · 问答切分（大模型）。

把转写切成「问题-回答」对，供 s6 逐题评分。

**为什么靠内容而不是靠标签**：上游 s4 已判定这场录音的说话人标签可信度为 low
（实测 125 句切成 52 个回合，40% 只有一句）。所以这里不能按「speaker_id 的连续
片段」来切，只能靠内容 —— 疑问句、话轮转换、话题变化。

**为什么让模型只返回序号**：模型给出每个问答对由哪些序号组成，文本由程序还原。
好处是输出极小（没有截断风险）、模型没有机会改写原文、引用精确到句。
代价是模型必须准确追踪序号，因此这里对返回值做严格校验：
序号必须存在、不能被两个问答对重复占用。

校验不通过的序号会被丢弃并记入 warnings，而不是让整批失败 ——
切分结果不完美的代价，远小于整个阶段重跑。
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
    SpeakerMapping,
    Transcript,
    TranscriptSegment,
)
from app.pipeline.stages.base import Stage, StageContext, StageError
from app.prompts import get as get_prompt
from app.services import llm_log
from app.services.llm_deepseek import DeepSeekClient

logger = logging.getLogger(__name__)

# 整篇送进去。1M 上下文足以放下两小时的面试转写（约 8 万 token），
# 不需要分批 —— 分批反而会把跨批的问答对切断。
MAX_TRANSCRIPT_CHARS = 300000

# 切分结果通常不大，但思考模式的推理内容也占额度
MAX_OUTPUT_TOKENS = 32000


class QaSegmentationStage(Stage):
    name = "s5_qa_segmentation"
    label = "切分问答对"

    async def compute_input_hash(self, ctx: StageContext) -> str:
        prompt = get_prompt("qa_segmentation")
        with Session(get_engine()) as session:
            transcript = session.exec(
                select(Transcript).where(Transcript.interview_id == ctx.interview_id)
            ).first()
            mappings = session.exec(
                select(SpeakerMapping).where(
                    SpeakerMapping.interview_id == ctx.interview_id
                )
            ).all()

        if transcript is None:
            raise StageError("找不到转写结果，请先执行 s2_transcribe")

        text = transcript.corrected_text or transcript.text
        # 角色映射变了，切分也应该重跑（归属会变）
        roles = sorted((m.speaker_raw_id, m.role) for m in mappings)
        settings = get_settings()
        return hashlib.sha256(
            f"{hashlib.sha256(text.encode()).hexdigest()}|{roles}"
            f"|{prompt.version}|{settings.deepseek_model}".encode()
        ).hexdigest()

    async def execute(self, ctx: StageContext) -> dict[str, Any]:
        prompt = get_prompt("qa_segmentation")
        settings = get_settings()

        with Session(get_engine()) as session:
            interview = session.get(Interview, ctx.interview_id)
            transcript = session.exec(
                select(Transcript).where(Transcript.interview_id == ctx.interview_id)
            ).first()
            if transcript is None:
                raise StageError("找不到转写结果，请先执行 s2_transcribe")

            segments = session.exec(
                select(TranscriptSegment)
                .where(TranscriptSegment.transcript_id == transcript.id)
                .order_by(TranscriptSegment.seq)
            ).all()

            mappings = session.exec(
                select(SpeakerMapping).where(
                    SpeakerMapping.interview_id == ctx.interview_id
                )
            ).all()

        if not segments:
            raise StageError("转写结果里没有任何句子")

        rows = [
            {
                "seq": seg.seq,
                "speaker": seg.speaker_raw_id,
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "text": (seg.corrected_text or seg.text).strip(),
            }
            for seg in segments
        ]

        role_map = {m.speaker_raw_id: m.role for m in mappings}
        role_hint = (
            "、".join(
                f"说话人{k} ≈ {'面试官' if v == 'interviewer' else '候选人' if v == 'candidate' else '未定'}"
                for k, v in sorted(role_map.items())
            )
            or "未做角色判定"
        )

        # 上游 s4 写进阶段产物的标签可信度，这里读出来传给模型
        label_reliability = self._read_label_reliability(ctx) or "unknown"

        duration_minutes = max((r["end_ms"] for r in rows), default=0) / 60000

        ctx.emit("正在切分问答对…", 96)

        try:
            client = DeepSeekClient()
        except ConfigError as exc:
            raise StageError(str(exc)) from exc

        user_prompt = prompt.render(
            position_hint=(interview.position if interview and interview.position else "未提供"),
            role_hint=role_hint,
            label_reliability=label_reliability,
            segment_count=len(rows),
            duration_minutes=duration_minutes,
            transcript=self._build_transcript(rows, MAX_TRANSCRIPT_CHARS),
        )

        try:
            data, result = await client.chat_json(
                prompt.system, user_prompt, thinking=True, max_tokens=MAX_OUTPUT_TOKENS
            )
        except (LlmError, LlmJsonError) as exc:
            raise StageError(f"问答切分失败：{exc}", retryable=exc.retryable) from exc

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

        pairs, warnings = self._validate(data, rows)

        if not pairs:
            raise StageError(
                "问答切分没有产出任何有效结果",
                retryable=True,
            )

        coverage = len({s for p in pairs for s in p["question_seqs"] + p["answer_seqs"]})
        self._persist(ctx, pairs, rows)

        logger.info(
            "问答切分完成：%d 个问答对，覆盖 %d/%d 句，警告 %d 条",
            len(pairs),
            coverage,
            len(rows),
            len(warnings),
        )
        ctx.emit(f"切分出 {len(pairs)} 个问答对", 99)

        return {
            "prompt_version": prompt.version,
            "pair_count": len(pairs),
            "covered_segments": coverage,
            "total_segments": len(rows),
            "coverage_ratio": round(coverage / len(rows), 3),
            "warnings": warnings,
            "pairs": [
                {
                    "seq": p["seq"],
                    "topic": p["topic"],
                    "question_seqs": p["question_seqs"],
                    "answer_seqs": p["answer_seqs"],
                    "is_followup": p["is_followup"],
                    "is_off_topic": p["is_off_topic"],
                }
                for p in pairs
            ],
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "cost_estimate": result.cost_estimate,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _read_label_reliability(ctx: StageContext) -> str | None:
        """从 s4 的阶段产物里读出标签可信度，作为本次切分的上下文。"""
        with Session(get_engine()) as session:
            stage_row = session.exec(
                select(PipelineStage)
                .where(PipelineStage.run_id == ctx.run_id)
                .where(PipelineStage.stage == "s4_role_mapping")
            ).first()

        if stage_row is None or not stage_row.output_json:
            return None
        try:
            return json.loads(stage_row.output_json).get("label_reliability")
        except (ValueError, AttributeError):
            return None

    def _validate(
        self, data: dict[str, Any], rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """校验并规整模型返回的切分结果。

        逐条检查序号是否存在、是否被重复占用。不合格的序号直接丢弃并记警告 ——
        切分不完美的代价远小于整阶段重跑。
        """
        raw_pairs = data.get("qa_pairs")
        if not isinstance(raw_pairs, list):
            raise StageError("切分结果里 qa_pairs 不是数组", retryable=True)

        valid = {r["seq"] for r in rows}
        used: set[int] = set()
        warnings: list[str] = []
        pairs: list[dict[str, Any]] = []

        def take(value: Any, label: str, pair_index: int) -> list[int]:
            if not isinstance(value, list):
                return []
            result: list[int] = []
            for item in value:
                try:
                    seq = int(item)
                except (TypeError, ValueError):
                    warnings.append(f"第 {pair_index} 对 {label} 含非整数序号 {item!r}")
                    continue
                if seq not in valid:
                    warnings.append(f"第 {pair_index} 对 {label} 含不存在的序号 {seq}")
                    continue
                if seq in used:
                    warnings.append(f"序号 {seq} 被多个问答对占用，已归给先出现的那个")
                    continue
                used.add(seq)
                result.append(seq)
            return sorted(result)

        seen_pair_seq: set[int] = set()
        for index, item in enumerate(raw_pairs, 1):
            if not isinstance(item, dict):
                continue

            try:
                pair_seq = int(item.get("seq") or index)
            except (TypeError, ValueError):
                pair_seq = index
            if pair_seq in seen_pair_seq:
                pair_seq = index
            seen_pair_seq.add(pair_seq)

            q_seqs = take(item.get("question_seqs"), "question_seqs", index)
            a_seqs = take(item.get("answer_seqs"), "answer_seqs", index)

            if not q_seqs and not a_seqs:
                warnings.append(f"第 {index} 对没有任何有效序号，已丢弃")
                continue

            parent = item.get("parent_seq")
            try:
                parent_seq = int(parent) if parent is not None else None
            except (TypeError, ValueError):
                parent_seq = None

            pairs.append(
                {
                    "seq": pair_seq,
                    "topic": str(item.get("topic") or "").strip()[:60] or None,
                    "question_seqs": q_seqs,
                    "answer_seqs": a_seqs,
                    "is_followup": bool(item.get("is_followup")),
                    "parent_qa_seq": parent_seq,
                    "is_off_topic": bool(item.get("is_off_topic")),
                }
            )

        # 按时间顺序重排（用第一句的序号代表位置）
        pairs.sort(key=lambda p: (p["question_seqs"] or p["answer_seqs"])[0])

        # 重排后重新编号，保证 seq 连续且与顺序一致
        for new_seq, pair in enumerate(pairs, 1):
            pair["seq"] = new_seq

        return pairs, warnings

    @staticmethod
    def _build_transcript(rows: list[dict[str, Any]], max_chars: int) -> str:
        lines = []
        for row in rows:
            minutes, seconds = divmod(int(row["start_ms"] / 1000), 60)
            lines.append(
                f"{row['seq']} [说话人{row['speaker']}] [{minutes:02d}:{seconds:02d}] {row['text']}"
            )
        full = "\n".join(lines)
        if len(full) > max_chars:  # pragma: no cover - 两小时面试也到不了这个量级
            logger.warning("转写超过 %d 字符，可能影响切分质量", max_chars)
        return full

    def _persist(
        self,
        ctx: StageContext,
        pairs: list[dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> None:
        by_seq = {r["seq"]: r for r in rows}

        with Session(get_engine()) as session:
            # 重跑时先清旧的。QaAnalysis 有外键指向 QaPair，必须先删分析。
            old_pairs = session.exec(
                select(QaPair).where(QaPair.interview_id == ctx.interview_id)
            ).all()
            if old_pairs:
                old_ids = [p.id for p in old_pairs]
                old_analyses = session.exec(
                    select(QaAnalysis).where(QaAnalysis.interview_id == ctx.interview_id)
                ).all()
                for analysis in old_analyses:
                    session.delete(analysis)
                session.commit()
                for pair in old_pairs:
                    session.delete(pair)
                session.commit()
                logger.debug("清理了 %d 个旧问答对", len(old_ids))

            for pair in pairs:
                q_rows = [by_seq[s] for s in pair["question_seqs"]]
                a_rows = [by_seq[s] for s in pair["answer_seqs"]]

                question_text = "".join(r["text"] for r in q_rows).strip()
                answer_text = "".join(r["text"] for r in a_rows).strip()

                all_rows = q_rows + a_rows
                asker = q_rows[0]["speaker"] if q_rows else None
                answerer = a_rows[0]["speaker"] if a_rows else None

                session.add(
                    QaPair(
                        interview_id=ctx.interview_id,
                        seq=pair["seq"],
                        topic=pair["topic"],
                        question_text=question_text,
                        answer_text=answer_text,
                        asker_raw_id=asker,
                        answerer_raw_id=answerer,
                        start_ms=min((r["start_ms"] for r in all_rows), default=None),
                        end_ms=max((r["end_ms"] for r in all_rows), default=None),
                        is_followup=pair["is_followup"],
                        parent_qa_seq=pair["parent_qa_seq"],
                        is_off_topic=pair["is_off_topic"],
                    )
                )
            session.commit()
