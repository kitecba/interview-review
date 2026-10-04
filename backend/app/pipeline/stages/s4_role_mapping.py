"""s4 · 角色判定（大模型）。

**这个阶段要回答的问题**：ASR 返回的匿名编号（说话人0 / 说话人1）里，
谁是面试官、谁是候选人。

**为什么不能简单按标签分组投票**：真实面试录音实测，125 句被切成 52 个
"发言回合"，其中 40% 只有一句话 —— 标签在句级别频繁跳变。具体捕捉到的错误是
面试官的一句话被从中间切开、两半分属不同标签，紧接着候选人的回答又和面试官的
提问同属一个标签。

所以这里的做法是：**让模型看内容判断整体角色**，把标签降级成弱旁证；
同时要求它报告标签的可信度（`label_reliability`），这个字段会直接影响下游
s5 问答切分的策略。

**兜底**：模型置信度低于阈值时，用启发式规则重判 —— 提问多、句子短的那个是面试官。
这个规则不精确，但比"随机猜一个"强，而且结果会标记为需要人工确认。
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
from app.db.models import Interview, SpeakerMapping, Transcript, TranscriptSegment
from app.pipeline.stages.base import Stage, StageContext, StageError
from app.prompts import get as get_prompt
from app.services import llm_log
from app.services.llm_deepseek import DeepSeekClient

logger = logging.getLogger(__name__)

# 送给模型的文本上限。超出后做头尾保留 + 中间等距抽样。
# 角色判定只需要"整体印象"，不需要逐句精读。
MAX_TRANSCRIPT_CHARS = 60000

# 置信度低于这个值就认为模型没把握，走启发式兜底
CONFIDENCE_THRESHOLD = 0.6

# 面试官的典型措辞。仅用于兜底启发式，不参与正常路径。
INTERVIEWER_MARKERS = (
    "你能说一下", "介绍一下", "你觉得", "你刚才", "你有什么想问",
    "具体是", "为什么", "是什么", "那你", "说说",
)


class RoleMappingStage(Stage):
    name = "s4_role_mapping"
    label = "区分说话人角色"

    async def compute_input_hash(self, ctx: StageContext) -> str:
        prompt = get_prompt("role_mapping")
        with Session(get_engine()) as session:
            transcript = session.exec(
                select(Transcript).where(Transcript.interview_id == ctx.interview_id)
            ).first()
        if transcript is None:
            raise StageError("找不到转写结果，请先执行 s2_transcribe")

        # 用纠错后的文本（如果有）参与计算：纠错改了内容，角色判定就该重跑
        text = transcript.corrected_text or transcript.text
        settings = get_settings()
        return hashlib.sha256(
            f"{hashlib.sha256(text.encode()).hexdigest()}"
            f"|{prompt.version}|{settings.deepseek_model}".encode()
        ).hexdigest()

    async def execute(self, ctx: StageContext) -> dict[str, Any]:
        prompt = get_prompt("role_mapping")
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

        if not segments:
            raise StageError("转写结果里没有任何句子")

        # 优先用纠错后的文本 —— s3 存在的意义就是让下游读到更可靠的文字
        rows = [
            {
                "seq": seg.seq,
                "speaker": seg.speaker_raw_id,
                "start_ms": seg.start_ms,
                "text": seg.corrected_text or seg.text,
            }
            for seg in segments
        ]

        speaker_ids = sorted({row["speaker"] for row in rows})
        duration_minutes = max((row["start_ms"] for row in rows), default=0) / 60000

        ctx.emit("正在判断谁是面试官…", 93)

        try:
            client = DeepSeekClient()
        except ConfigError as exc:
            raise StageError(str(exc)) from exc

        user_prompt = prompt.render(
            position_hint=(interview.position if interview and interview.position else "未提供"),
            duration_minutes=duration_minutes,
            segment_count=len(rows),
            speaker_ids=speaker_ids,
            transcript=self._build_transcript(rows, MAX_TRANSCRIPT_CHARS),
        )

        try:
            data, result = await client.chat_json(
                prompt.system, user_prompt, thinking=True, max_tokens=8000
            )
        except (LlmError, LlmJsonError) as exc:
            raise StageError(f"角色判定失败：{exc}", retryable=exc.retryable) from exc

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

        roles, used_fallback = self._resolve_roles(data, rows, speaker_ids)

        label_reliability = str(data.get("label_reliability") or "unknown")
        label_issues = data.get("label_issues") or None
        is_multi = bool(data.get("is_multi_candidate"))
        needs_review = bool(data.get("needs_manual_review")) or used_fallback

        self._persist(ctx, roles)

        logger.info(
            "角色判定完成：%s（标签可信度=%s%s）",
            {f"说话人{k}": v["role"] for k, v in roles.items()},
            label_reliability,
            "，已走启发式兜底" if used_fallback else "",
        )
        ctx.emit(
            "、".join(
                f"说话人{k} = {'面试官' if v['role'] == 'interviewer' else '候选人' if v['role'] == 'candidate' else '未确定'}"
                for k, v in sorted(roles.items())
            ),
            98,
        )

        return {
            "prompt_version": prompt.version,
            "speakers": [
                {"speaker_raw_id": k, **v} for k, v in sorted(roles.items())
            ],
            "is_multi_candidate": is_multi,
            "label_reliability": label_reliability,
            "label_issues": label_issues,
            "needs_manual_review": needs_review,
            "used_heuristic_fallback": used_fallback,
            "notes": data.get("notes"),
        }

    # ------------------------------------------------------------------
    def _resolve_roles(
        self,
        data: dict[str, Any],
        rows: list[dict[str, Any]],
        speaker_ids: list[int],
    ) -> tuple[dict[int, dict[str, Any]], bool]:
        """确定每个说话人的角色，必要时走启发式兜底。

        返回 (roles, used_fallback)。
        """
        resolved: dict[int, dict[str, Any]] = {}

        raw_speakers = data.get("speakers")
        if isinstance(raw_speakers, list):
            for item in raw_speakers:
                if not isinstance(item, dict):
                    continue
                try:
                    spk = int(item.get("speaker_raw_id"))
                except (TypeError, ValueError):
                    continue
                role = str(item.get("role") or "unknown").lower()
                if role not in ("interviewer", "candidate", "unknown"):
                    role = "unknown"
                confidence = float(item.get("confidence") or 0.0)
                resolved[spk] = {
                    "role": role,
                    "confidence": round(confidence, 3),
                    "evidence": (item.get("evidence") or "")[:500],
                    "source": "llm",
                }

        # 模型可能漏掉某些编号；漏掉的一律先标 unknown
        for spk in speaker_ids:
            resolved.setdefault(
                spk,
                {"role": "unknown", "confidence": 0.0, "evidence": "", "source": "missing"},
            )

        # 判定「是否可用」：至少要有一个面试官和一个候选人，且置信度达标
        confident = [
            r for r in resolved.values()
            if r["confidence"] >= CONFIDENCE_THRESHOLD and r["role"] != "unknown"
        ]
        has_interviewer = any(r["role"] == "interviewer" for r in confident)
        has_candidate = any(r["role"] == "candidate" for r in confident)
        usable = has_interviewer and has_candidate

        if usable:
            return resolved, False

        # --- 兜底 ---
        logger.warning(
            "模型判定不可用（面试官=%s 候选人=%s），改用启发式规则",
            has_interviewer,
            has_candidate,
        )
        heuristic = self._heuristic_roles(rows)
        for spk, (role, confidence, reason) in heuristic.items():
            resolved[spk] = {
                "role": role,
                "confidence": round(confidence, 3),
                "evidence": f"[启发式兜底] {reason}",
                "source": "heuristic",
            }
        return resolved, True

    @staticmethod
    def _heuristic_roles(
        rows: list[dict[str, Any]],
    ) -> dict[int, tuple[str, float, str]]:
        """兜底规则：提问多、句子短的那个是面试官。

        不精确，但比随机猜强。判定结果会被标记为来源 heuristic，
        并在 needs_manual_review 里提示人工确认。
        """
        stats: dict[int, dict[str, float]] = {}
        for row in rows:
            text = row["text"] or ""
            s = stats.setdefault(row["speaker"], {"n": 0, "q": 0, "chars": 0, "markers": 0})
            s["n"] += 1
            s["chars"] += len(text)
            if "？" in text or "?" in text:
                s["q"] += 1
            if any(m in text for m in INTERVIEWER_MARKERS):
                s["markers"] += 1

        if not stats:
            return {}

        scored: list[tuple[int, float]] = []
        for spk, s in stats.items():
            n = max(s["n"], 1)
            avg_len = s["chars"] / n
            # 疑问句比例和标志性措辞占比越高越像面试官；平均句长越长越像候选人
            score = (s["q"] / n) * 2.0 + (s["markers"] / n) * 2.0 - (avg_len / 60.0)
            scored.append((spk, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        interviewer = scored[0][0]

        out: dict[int, tuple[str, float, str]] = {}
        for spk, score in scored:
            n = stats[spk]["n"]
            avg_len = stats[spk]["chars"] / max(n, 1)
            if spk == interviewer:
                out[spk] = (
                    "interviewer",
                    0.5,
                    f"疑问句 {stats[spk]['q']}/{n}，标志性措辞 {stats[spk]['markers']} 处，平均 {avg_len:.0f} 字",
                )
            else:
                out[spk] = (
                    "candidate",
                    0.5,
                    f"陈述为主，平均 {avg_len:.0f} 字，发言 {n} 句",
                )
        return out

    @staticmethod
    def _build_transcript(rows: list[dict[str, Any]], max_chars: int) -> str:
        """拼出送给模型的文本；过长时做头尾保留 + 中间抽样。

        角色判定要的是整体印象，抽样足够。开头尤其重要 ——
        "请先做个自我介绍"几乎必然出现在最前面。
        """
        lines = []
        for row in rows:
            minutes, seconds = divmod(int(row["start_ms"] / 1000), 60)
            lines.append(
                f"{row['seq']} [说话人{row['speaker']}] [{minutes:02d}:{seconds:02d}] {row['text']}"
            )

        full = "\n".join(lines)
        if len(full) <= max_chars:
            return full

        # 头尾各留 35%，中间 30% 等距抽样
        head_count = int(len(lines) * 0.35)
        tail_count = int(len(lines) * 0.35)
        middle = lines[head_count : len(lines) - tail_count]
        budget = max(max_chars - len("\n".join(lines[:head_count] + lines[-tail_count:])), 0)
        step = max(len(middle) // max(budget // 80, 1), 1)
        sampled = middle[::step]

        omitted = len(middle) - len(sampled)
        logger.info("转写过长，中间省略 %d 句（保留首尾）", omitted)
        return "\n".join(
            lines[:head_count]
            + [f"…（此处省略 {omitted} 句）…"]
            + sampled
            + lines[-tail_count:]
        )

    def _persist(self, ctx: StageContext, roles: dict[int, dict[str, Any]]) -> None:
        """写入逐说话人的角色映射。人工改判过的行不会被覆盖。

        整场级的结论（标签可信度、是否需要人工确认）放在阶段产物 output_json 里，
        不进这张表 —— 它是逐说话人的结构，不适合承载整场级的信息。
        """
        with Session(get_engine()) as session:
            existing = session.exec(
                select(SpeakerMapping).where(
                    SpeakerMapping.interview_id == ctx.interview_id
                )
            ).all()
            manual = {
                row.speaker_raw_id for row in existing if row.manual_override
            }

            # 清掉旧的自动判定结果，保留人工改判的
            for row in existing:
                if not row.manual_override:
                    session.delete(row)
            if existing:
                session.commit()

            for spk, info in roles.items():
                if spk in manual:
                    logger.debug("说话人 %d 已有人工改判，跳过自动结果", spk)
                    continue
                session.add(
                    SpeakerMapping(
                        interview_id=ctx.interview_id,
                        chunk_index=0,
                        speaker_raw_id=spk,
                        role=info["role"],
                        confidence=info["confidence"],
                        evidence=info["evidence"],
                        manual_override=False,
                    )
                )
            session.commit()
