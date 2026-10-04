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
        self._persist(ctx, pairs)

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
        """校验模型返回的切分结果，并组装出问题/回答文本。

        三层校验：
        1. 序号必须存在，且不能被两个问答对重复占用。
        2. `boundary` 里的 `seq` 必须属于该问答对。
        3. `boundary.question_prefix` 必须是该句开头的**逐字前缀** ——
           这是防止模型改写到原文的手段。对不上就整条忽略。

        不合格的输入丢弃并记警告，而不是让整阶段失败：
        切分不完美的代价远小于整个阶段重跑。
        """
        raw_pairs = data.get("qa_pairs")
        if not isinstance(raw_pairs, list):
            raise StageError("切分结果里 qa_pairs 不是数组", retryable=True)

        by_seq = {r["seq"]: r for r in rows}
        valid = set(by_seq)
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

        for index, item in enumerate(raw_pairs, 1):
            if not isinstance(item, dict):
                continue

            q_seqs = take(item.get("question_seqs"), "question_seqs", index)
            a_seqs = take(item.get("answer_seqs"), "answer_seqs", index)

            if not q_seqs and not a_seqs:
                warnings.append(f"第 {index} 对没有任何有效序号，已丢弃")
                continue

            members = set(q_seqs) | set(a_seqs)
            splits = self._parse_boundary(
                item.get("boundary"), members, by_seq, index, warnings
            )

            member_rows = [by_seq[s] for s in sorted(members)]

            parent = item.get("parent_seq")
            try:
                parent_seq = int(parent) if parent is not None else None
            except (TypeError, ValueError):
                parent_seq = None

            pairs.append(
                {
                    "topic": str(item.get("topic") or "").strip()[:60] or None,
                    "question_seqs": q_seqs,
                    "answer_seqs": a_seqs,
                    "splits": splits,
                    "start_ms": min((r["start_ms"] for r in member_rows), default=None),
                    "end_ms": max((r["end_ms"] for r in member_rows), default=None),
                    "asker_raw_id": by_seq[q_seqs[0]]["speaker"] if q_seqs else None,
                    "answerer_raw_id": by_seq[a_seqs[0]]["speaker"] if a_seqs else None,
                    "is_followup": bool(item.get("is_followup")),
                    "parent_qa_seq": parent_seq,
                    "is_off_topic": bool(item.get("is_off_topic")),
                }
            )

        # 按时间顺序重排，并重新编号保证 seq 连续
        pairs.sort(key=lambda p: (p["question_seqs"] or p["answer_seqs"])[0])
        for new_seq, pair in enumerate(pairs, 1):
            pair["seq"] = new_seq

        # 收养漏掉的句子，然后在最后统一组装文本
        adopted = self._adopt_orphans(pairs, valid, warnings)
        for pair in pairs:
            self._assemble(pair, by_seq)

        if adopted:
            warnings.append(
                f"有 {adopted} 句未被模型归属，已自动并入前一个问答对。"
                "这些句子可能是语气词，也可能是被遗漏的回答内容，建议核对。"
            )

        return pairs, warnings

    @staticmethod
    def _adopt_orphans(
        pairs: list[dict[str, Any]], valid: set[int], warnings: list[str]
    ) -> int:
        """把模型漏掉的句子挂到前面最近的问答对上。

        为什么不让它们空着：漏掉的可能不是语气词，而是候选人的实质回答
        （实测出现过整整两句讲工具封装的内容被漏掉）。内容不进评分，
        比归错侧更糟 —— 前者是反馈不完整，后者至少还能被人工看见并纠正。

        归属规则：并入**前面最近的**已归属句所在的那一侧。面试是顺序进行的，
        紧邻的上下文是最可靠的线索。
        """
        side_of: dict[int, str] = {}
        pair_of: dict[int, dict[str, Any]] = {}
        for pair in pairs:
            for seq in pair["question_seqs"]:
                side_of[seq] = "question_seqs"
                pair_of[seq] = pair
            for seq in pair["answer_seqs"]:
                side_of[seq] = "answer_seqs"
                pair_of[seq] = pair

        orphans = sorted(valid - set(side_of))
        if not orphans:
            return 0

        assigned = sorted(side_of)
        adopted = 0
        for orphan in orphans:
            # 前面最近的一句；没有则退而取后面最近的一句
            prev = [s for s in assigned if s < orphan]
            if prev:
                anchor = prev[-1]
            else:
                following = [s for s in assigned if s > orphan]
                if not following:
                    warnings.append(f"序号 {orphan} 无法归属到任何问答对，已丢弃")
                    continue
                anchor = following[0]

            pair = pair_of.get(anchor)
            if pair is None:
                continue
            pair[side_of[anchor]].append(orphan)
            side_of[orphan] = side_of[anchor]
            pair_of[orphan] = pair
            adopted += 1

        for pair in pairs:
            pair["question_seqs"].sort()
            pair["answer_seqs"].sort()

        return adopted

    @staticmethod
    def _assemble(pair: dict[str, Any], by_seq: dict[int, dict[str, Any]]) -> None:
        """按序号顺序组装出问题与回答文本。

        被 boundary 切开的句子贡献两段：前缀归问题、剩余归回答 ——
        与它原本被列在哪一侧无关。
        """
        splits: dict[int, str] = pair.get("splits") or {}
        members = sorted(set(pair["question_seqs"]) | set(pair["answer_seqs"]))

        q_parts: list[str] = []
        a_parts: list[str] = []
        for seq in members:
            text = by_seq[seq]["text"]
            prefix = splits.get(seq)
            if prefix is not None:
                q_parts.append(prefix)
                remainder = text[len(prefix) :]
                if remainder.strip():
                    a_parts.append(remainder)
            elif seq in pair["question_seqs"]:
                q_parts.append(text)
            else:
                a_parts.append(text)

        pair["question_text"] = "".join(q_parts).strip()
        pair["answer_text"] = "".join(a_parts).strip()

    @staticmethod
    def _parse_boundary(
        raw: Any,
        members: set[int],
        by_seq: dict[int, dict[str, Any]],
        pair_index: int,
        warnings: list[str],
    ) -> dict[int, str]:
        """解析 boundary 字段，返回 {序号: 属于问题的前缀}。

        只接受「前缀与原文逐字一致」的条目。对不上就忽略 ——
        这道校验让模型无法借 boundary 之名去改写原文。
        """
        if not isinstance(raw, list):
            return {}

        result: dict[int, str] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                seq = int(entry.get("seq"))
            except (TypeError, ValueError):
                warnings.append(f"第 {pair_index} 对 boundary 含无效序号 {entry.get('seq')!r}")
                continue

            prefix = entry.get("question_prefix")
            if not isinstance(prefix, str) or not prefix:
                warnings.append(f"第 {pair_index} 对 boundary 第 {seq} 句缺少 question_prefix")
                continue
            if seq not in members:
                warnings.append(f"第 {pair_index} 对 boundary 的序号 {seq} 不属于该问答对，已忽略")
                continue

            text = by_seq[seq]["text"]
            if not text.startswith(prefix):
                # 前缀对不上：模型想改写的不是原句开头，拒绝。
                warnings.append(
                    f"第 {pair_index} 对 boundary 第 {seq} 句的 question_prefix "
                    f"与原文开头不一致，已忽略（原文开头：「{text[:20]}」）"
                )
                continue
            if len(prefix) >= len(text):
                # 整句都属于问题，那就没必要切分
                continue

            result[seq] = prefix

        return result

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

    def _persist(self, ctx: StageContext, pairs: list[dict[str, Any]]) -> None:
        """写入问答对。文本已在 _validate 阶段组装好（含 boundary 切分）。"""
        with Session(get_engine()) as session:
            # 重跑时先清旧的。QaAnalysis 有外键指向 QaPair，必须先删分析。
            old_pairs = session.exec(
                select(QaPair).where(QaPair.interview_id == ctx.interview_id)
            ).all()
            if old_pairs:
                old_analyses = session.exec(
                    select(QaAnalysis).where(QaAnalysis.interview_id == ctx.interview_id)
                ).all()
                for analysis in old_analyses:
                    session.delete(analysis)
                session.commit()
                for pair in old_pairs:
                    session.delete(pair)
                session.commit()
                logger.debug("清理了 %d 个旧问答对", len(old_pairs))

            for pair in pairs:
                session.add(
                    QaPair(
                        interview_id=ctx.interview_id,
                        seq=pair["seq"],
                        topic=pair["topic"],
                        question_text=pair["question_text"],
                        answer_text=pair["answer_text"],
                        asker_raw_id=pair["asker_raw_id"],
                        answerer_raw_id=pair["answerer_raw_id"],
                        start_ms=pair["start_ms"],
                        end_ms=pair["end_ms"],
                        is_followup=pair["is_followup"],
                        parent_qa_seq=pair["parent_qa_seq"],
                        is_off_topic=pair["is_off_topic"],
                    )
                )
            session.commit()
