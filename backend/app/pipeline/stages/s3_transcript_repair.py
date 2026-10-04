"""s3 · 转写纠错（大模型）。

**为什么需要这一步**：ASR 对中英混合的技术名词识别很差。真实面试录音实测：
`SQL → circle`（全文反复出现）、`LangGraph → long graph`、`RAG → R G`、
`Agent → A`、`精排 → 金牌`。其中 `circle` 这类错误虽然明显，但 `key → 接着`
那类"读起来通顺、语义全错"的错误更危险 —— 下游大模型很可能察觉不到。

**为什么让模型返回改动清单而不是改写后的全文**：

1. 可核对 —— 用户能逐条看到改了什么、为什么，而不是面对一份不知改了哪里的文本。
2. 降低过度修改的危害 —— 大模型有润色冲动，如果允许它返回全文，它可能顺手把
   口语化的表达"修"得更书面，那就成了篡改而不是纠错。只接受「原文→修正」的
   片段级改动，越界会立刻暴露（因为做不进去）。

**应用改动用的是子串替换**，这本身就是一道防线：模型给的 `original` 必须在原句里
逐字出现，否则这条改动会被拒绝并记入 skipped。模型没法凭空重写整句。

**已知取舍**：分批处理，某一批失败会导致整个阶段重跑（重跑时已完成的分批也会
重新调用）。这是为了保持实现简单 —— 一次纠错的总成本约 ¥0.05，重跑的浪费有限。
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
from app.db.models import Transcript, TranscriptSegment
from app.pipeline.stages.base import Stage, StageContext, StageError
from app.prompts import get as get_prompt
from app.services import llm_log
from app.services.llm_deepseek import DeepSeekClient

logger = logging.getLogger(__name__)

# 每批送多少句。**不要调大** —— 一次实测教训：BATCH_SIZE=60 时，
# 输出 json 加上思考模式的推理内容超出了 max_tokens，被截断后无法解析，
# 整批失败。25 句一批时输出稳定。
BATCH_SIZE = 25
# 额外附上前若干句作为只读上下文，帮助模型判断代词指代和被截断的句子
CONTEXT_SIZE = 3
# 输出上限。思考模式的推理内容也占额度，必须给足。
MAX_OUTPUT_TOKENS = 16000


class TranscriptRepairStage(Stage):
    name = "s3_transcript_repair"
    label = "转写纠错"

    async def compute_input_hash(self, ctx: StageContext) -> str:
        prompt = get_prompt("repair")
        with Session(get_engine()) as session:
            transcript = session.exec(
                select(Transcript).where(Transcript.interview_id == ctx.interview_id)
            ).first()
        if transcript is None or not transcript.text:
            raise StageError("找不到转写结果，请先执行 s2_transcribe")

        # 提示词版本与模型名都参与计算：改了 prompt 或换了模型都会触发重跑
        settings = get_settings()
        return hashlib.sha256(
            f"{hashlib.sha256(transcript.text.encode()).hexdigest()}"
            f"|{prompt.version}|{settings.deepseek_model}|{BATCH_SIZE}".encode()
        ).hexdigest()

    async def execute(self, ctx: StageContext) -> dict[str, Any]:
        prompt = get_prompt("repair")

        with Session(get_engine()) as session:
            transcript = session.exec(
                select(Transcript).where(Transcript.interview_id == ctx.interview_id)
            ).first()
            if transcript is None or not transcript.text:
                raise StageError("找不到转写结果，请先执行 s2_transcribe")
            transcript_id = transcript.id

            segments = session.exec(
                select(TranscriptSegment)
                .where(TranscriptSegment.transcript_id == transcript_id)
                .order_by(TranscriptSegment.seq)
            ).all()

        if not segments:
            raise StageError("转写结果里没有任何句子")

        # 把需要的字段拷出来，避免在异步调用期间持有 session
        rows = [
            {
                "seq": seg.seq,
                "speaker": seg.speaker_raw_id,
                "start_ms": seg.start_ms,
                "text": seg.text,
            }
            for seg in segments
        ]

        batches = [
            rows[i : i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)
        ]
        total_batches = len(batches)
        logger.info(
            "开始纠错：%d 句，分 %d 批，模型 %s，prompt %s",
            len(rows),
            total_batches,
            get_settings().deepseek_model,
            prompt.version,
        )

        try:
            client = DeepSeekClient()
        except ConfigError as exc:
            raise StageError(str(exc)) from exc

        all_corrections: list[dict[str, Any]] = []
        llm_failed_batches: list[int] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for index, batch in enumerate(batches):
            if ctx.is_canceled():
                raise StageError("任务已取消")

            batch_no = index + 1
            ctx.emit(
                f"正在校对第 {batch_no}/{total_batches} 批…",
                70 + int(index / total_batches * 20),
            )

            context_rows = rows[max(0, index * BATCH_SIZE - CONTEXT_SIZE) : index * BATCH_SIZE]

            try:
                corrections, usage = await self._repair_batch(
                    client, prompt, batch, context_rows, batch_no, total_batches
                )
            except (LlmError, LlmJsonError) as exc:
                # 单批失败不致命中止整个阶段：其余批次的结果仍然有价值。
                # 失败的批次会被记下来，前端提示用户重跑即可。
                logger.warning("第 %d 批纠错失败：%s", batch_no, exc)
                llm_failed_batches.append(batch_no)
                continue

            total_prompt_tokens += usage[0]
            total_completion_tokens += usage[1]
            all_corrections.extend(corrections)

        applied, skipped = self._apply_corrections(rows, all_corrections)

        # 写回：只为真正改过的句子写 corrected_text，其余保持 None，
        # 前端据此只高亮被改过的部分
        self._persist(ctx, transcript_id, rows, applied)

        cost = total_prompt_tokens / 1e6 * 0.30 + total_completion_tokens / 1e6 * 1.20
        llm_log.record_call(
            stage=self.name,
            model=get_settings().deepseek_model,
            prompt_version=prompt.version,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            cost_estimate=cost,
            latency_ms=0,
            interview_id=ctx.interview_id,
            run_id=ctx.run_id,
            status="ok" if not llm_failed_batches else "partial",
        )

        logger.info(
            "纠错完成：提出 %d 条改动，成功应用 %d 条，跳过 %d 条，失败批次 %s",
            len(all_corrections),
            len(applied),
            len(skipped),
            llm_failed_batches or "无",
        )
        ctx.emit(f"纠错完成：修正了 {len(applied)} 处", 92)

        return {
            "prompt_version": prompt.version,
            "batch_count": total_batches,
            "failed_batches": llm_failed_batches,
            "proposed_count": len(all_corrections),
            "applied_count": len(applied),
            "skipped_count": len(skipped),
            # 完整改动清单，供界面展示与人工核对
            "corrections": applied,
            "skipped": skipped,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "cost_estimate": cost,
        }

    # ------------------------------------------------------------------
    async def _repair_batch(
        self,
        client: DeepSeekClient,
        prompt,
        batch: list[dict[str, Any]],
        context_rows: list[dict[str, Any]],
        batch_no: int,
        batch_total: int,
    ) -> tuple[list[dict[str, Any]], tuple[int, int]]:
        context_block = ""
        if context_rows:
            lines = "\n".join(self._format_row(r) for r in context_rows)
            context_block = f"（以下是上一批的内容，仅供参考上下文，不需要校对）\n\n{lines}\n\n"

        segments_text = "\n".join(self._format_row(r) for r in batch)

        user_prompt = prompt.render(
            batch_no=batch_no,
            batch_total=batch_total,
            context_block=context_block,
            segments=segments_text,
        )

        # 思考模式开着：判断"这个词在这里讲不讲得通"需要推理，不是模式匹配
        data, result = await client.chat_json(
            prompt.system, user_prompt, thinking=True, max_tokens=MAX_OUTPUT_TOKENS
        )

        raw = data.get("corrections")
        if not isinstance(raw, list):
            raise LlmJsonError("纠错结果里 corrections 不是数组", detail=json.dumps(data)[:300])

        # 只保留本批范围内的序号，防止模型引用上下文里的句子而误改上一批
        valid_seqs = {r["seq"] for r in batch}
        corrections = [
            c for c in raw if isinstance(c, dict) and c.get("seq") in valid_seqs
        ]

        return corrections, (result.prompt_tokens, result.completion_tokens)

    @staticmethod
    def _format_row(row: dict[str, Any]) -> str:
        minutes, seconds = divmod(int(row["start_ms"] / 1000), 60)
        return f"{row['seq']} [说话人{row['speaker']}] [{minutes:02d}:{seconds:02d}] {row['text']}"

    def _apply_corrections(
        self, rows: list[dict[str, Any]], corrections: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """把改动应用到句子上。返回 (成功应用的清单, 被拒绝的清单)。"""
        by_seq = {row["seq"]: row for row in rows}
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for item in corrections:
            seq = item.get("seq")
            original = item.get("original")
            corrected = item.get("corrected")

            if not isinstance(original, str) or not isinstance(corrected, str):
                skipped.append({**item, "skip_reason": "original/corrected 缺失或类型不对"})
                continue
            if not original or original == corrected:
                continue

            row = by_seq.get(seq)
            if row is None:
                skipped.append({**item, "skip_reason": "序号不在本批范围内"})
                continue

            # 当前文本（可能已经被同一批里的前一条改动改过）
            current = row.get("corrected_text") or row["text"]

            if original not in current:
                # 子串对不上：说明模型改写的不是原句里的片段，拒绝这条。
                # 这是防止模型"顺手润色"的主要防线。
                skipped.append(
                    {**item, "skip_reason": "original 未在原文中逐字出现，拒绝应用"}
                )
                continue

            row["corrected_text"] = current.replace(original, corrected)
            applied.append(
                {
                    "seq": seq,
                    "original": original,
                    "corrected": corrected,
                    "reason": item.get("reason", ""),
                    "start_ms": row["start_ms"],
                }
            )

        return applied, skipped

    def _persist(
        self,
        ctx: StageContext,
        transcript_id: str,
        rows: list[dict[str, Any]],
        applied: list[dict[str, Any]],
    ) -> None:
        changed_seqs = {item["seq"] for item in applied}

        with Session(get_engine()) as session:
            for row in rows:
                if row["seq"] not in changed_seqs:
                    continue
                seg = session.exec(
                    select(TranscriptSegment)
                    .where(TranscriptSegment.transcript_id == transcript_id)
                    .where(TranscriptSegment.seq == row["seq"])
                ).first()
                if seg is not None:
                    seg.corrected_text = row["corrected_text"]
                    session.add(seg)

            # 同时更新整篇的纠错后文本，下游阶段直接用它
            transcript = session.get(Transcript, transcript_id)
            if transcript is not None:
                transcript.corrected_text = "\n".join(
                    (row.get("corrected_text") or row["text"]) for row in rows
                )
                session.add(transcript)

            session.commit()
