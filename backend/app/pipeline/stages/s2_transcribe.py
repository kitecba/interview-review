"""s2 · 云端语音转写（阿里云百炼 paraformer-v2）。

**这个阶段是整个项目的成本红线。**

百炼按音频时长计费，重复提交就是重复花钱。所以这里有三条硬约束：

1. **task_id 一经返回立刻落库并 commit。** 之后的轮询失败、进程崩溃、用户重跑，
   都只能基于这个已保存的 task_id 重新轮询，绝不能重新提交。
2. **提交前先查库。** 如果这个面试已经存在 task_id 且转写还没完成，直接续轮询 ——
   这正是「断点续跑」在这里的意义。
3. **对话人编号是匿名的。** ASR 只给 speaker_id（0/1/2…），不告诉你谁是面试官，
   且编号不跨录音稳定。角色判定是 s3 的独立工作。

转写完成后会删掉本地的 wav —— D 盘只剩十几 GB，而这个文件动辄上百 MB，
OSS 上已经有副本了。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.errors import AsrError, AsrTimeoutError, ConfigError, StorageError
from app.db.engine import get_engine
from app.db.models import AudioAsset, Transcript, TranscriptSegment
from app.pipeline.stages.base import Stage, StageContext, StageError
from app.services import asr_bailian, storage_oss

logger = logging.getLogger(__name__)

# 默认按两人对话提示（面试官 + 候选人）。官方文档说明这只是个提示，
# 不保证结果一致，但实测能明显改善分离效果。
DEFAULT_SPEAKER_COUNT = 2


class TranscribeStage(Stage):
    name = "s2_transcribe"
    label = "语音转写"

    async def compute_input_hash(self, ctx: StageContext) -> str:
        with Session(get_engine()) as session:
            asset = session.exec(
                select(AudioAsset).where(AudioAsset.interview_id == ctx.interview_id)
            ).first()
        if asset is None:
            raise StageError("找不到该面试的音频记录")

        settings = get_settings()
        # 模型名参与计算：换了 ASR 模型就该重新转写
        return hashlib.sha256(
            f"{asset.sha256}|{settings.bailian_asr_model}|{DEFAULT_SPEAKER_COUNT}".encode()
        ).hexdigest()

    async def execute(self, ctx: StageContext) -> dict[str, Any]:
        settings = get_settings()

        with Session(get_engine()) as session:
            asset = session.exec(
                select(AudioAsset).where(AudioAsset.interview_id == ctx.interview_id)
            ).first()
            if asset is None:
                raise StageError("找不到该面试的音频记录")
            if not asset.oss_key:
                raise StageError("音频尚未上传到 OSS，请先执行 s1_upload_oss")

            oss_key = asset.oss_key
            duration_ms = asset.duration_ms
            local_path = Path(asset.local_path) if asset.local_path else None

            # 关键：查是否已有 task_id。有就续轮询，没有才提交。
            existing = session.exec(
                select(Transcript).where(Transcript.interview_id == ctx.interview_id)
            ).first()
            existing_task_id = existing.asr_task_id if existing else None
            existing_id = existing.id if existing else None

        try:
            client = asr_bailian.BailianAsrClient()
        except ConfigError as exc:
            raise StageError(str(exc)) from exc

        # ------------------------------------------------------------------
        # 第一步：拿到 task_id（复用已有的，或新提交一个）
        # ------------------------------------------------------------------
        task_id = existing_task_id

        if task_id:
            logger.info("复用已存在的转写任务，继续轮询：task_id=%s", task_id)
            ctx.emit("继续等待上次的转写任务…", 25)
        else:
            ctx.emit("正在生成音频访问链接…", 22)
            try:
                file_url = await storage_oss.signed_url(oss_key)
            except StorageError as exc:
                raise StageError(str(exc), retryable=True) from exc

            ctx.emit("正在提交转写任务…", 25)
            try:
                task_id = await client.submit(
                    file_url,
                    diarization=True,
                    speaker_count=DEFAULT_SPEAKER_COUNT,
                )
            except AsrError as exc:
                raise StageError(str(exc), retryable=exc.retryable) from exc

            # **立刻落库。** 这之后无论发生什么，都不会再有第二次提交。
            self._persist_task_id(ctx, task_id, existing_id, settings.bailian_asr_model)
            logger.info("转写任务已提交并落库：task_id=%s", task_id)

        # ------------------------------------------------------------------
        # 第二步：轮询。失败只重轮询，绝不重新提交。
        # ------------------------------------------------------------------
        def on_tick(status: str, waited: int) -> None:
            ctx.emit(f"转写中…（已等待 {waited} 秒，状态 {status}）", 30)

        try:
            task = await client.poll_until_done(task_id, duration_ms=duration_ms, on_tick=on_tick)
        except AsrTimeoutError as exc:
            raise StageError(str(exc), retryable=True) from exc
        except AsrError as exc:
            raise StageError(str(exc), retryable=exc.retryable) from exc

        if ctx.is_canceled():
            # 注意：任务已经提交并计费了。取消只影响本地处理，钱已经花了，
            # 所以这里不清理 task_id —— 下次重跑还能捡回这个结果。
            raise StageError("任务已取消（转写已在云端执行，费用已产生）")

        # ------------------------------------------------------------------
        # 第三步：拉取并解析结果
        # ------------------------------------------------------------------
        ctx.emit("正在拉取转写结果…", 55)

        results = (task.get("output") or {}).get("results") or []
        if not results:
            raise StageError("转写任务成功但未返回结果", retryable=True)

        first = results[0]
        subtask_status = first.get("subtask_status")
        if subtask_status and subtask_status != "SUCCEEDED":
            message = first.get("message") or first.get("code") or "未知原因"
            raise StageError(f"转写子任务失败（{subtask_status}）：{message}")

        transcription_url = first.get("transcription_url")
        if not transcription_url:
            raise StageError("转写结果里缺少 transcription_url", retryable=True)

        try:
            payload = await client.fetch_transcription(transcription_url)
        except AsrError as exc:
            raise StageError(str(exc), retryable=True) from exc

        result = asr_bailian.parse_transcription(payload, task_id=task_id)

        if not result.segments:
            raise StageError(
                "转写结果为空。若音频里确实有人说话，请检查录音是否损坏或音量过低。"
            )

        # ------------------------------------------------------------------
        # 第四步：落库
        # ------------------------------------------------------------------
        ctx.emit("正在保存转写结果…", 62)
        transcript_id = self._store_transcript(ctx, result, settings.bailian_asr_model)

        # 转写完成，本地 wav 没用了。D 盘只剩十几 GB，上百 MB 的文件必须清掉。
        self._cleanup_local_audio(local_path)

        speakers = result.speaker_ids
        logger.info(
            "转写完成：%d 句，识别出 %d 个说话人 %s，时长 %.1f 分钟",
            len(result.segments),
            len(speakers),
            speakers,
            result.duration_ms / 60000,
        )
        ctx.emit(
            f"转写完成：{len(result.segments)} 句，{len(speakers)} 个说话人", 68
        )

        return {
            "transcript_id": transcript_id,
            "task_id": task_id,
            "segment_count": len(result.segments),
            "speaker_ids": speakers,
            "duration_ms": result.duration_ms,
            "char_count": len(result.text),
        }

    # ------------------------------------------------------------------
    def _persist_task_id(
        self, ctx: StageContext, task_id: str, existing_id: str | None, model: str
    ) -> None:
        """把 task_id 立刻写进数据库。

        这是全项目最不能省的一次 commit —— 它是「不重复计费」的唯一凭据。
        """
        with Session(get_engine()) as session:
            transcript = session.get(Transcript, existing_id) if existing_id else None
            if transcript is None:
                transcript = Transcript(
                    interview_id=ctx.interview_id,
                    model=model,
                )
            transcript.asr_task_id = task_id
            session.add(transcript)
            session.commit()

    def _store_transcript(
        self, ctx: StageContext, result: asr_bailian.AsrResult, model: str
    ) -> str:
        with Session(get_engine()) as session:
            transcript = session.exec(
                select(Transcript).where(Transcript.interview_id == ctx.interview_id)
            ).first()
            if transcript is None:
                transcript = Transcript(interview_id=ctx.interview_id)

            transcript.asr_task_id = result.task_id
            transcript.model = model
            transcript.duration_ms = result.duration_ms
            transcript.speaker_count = result.speaker_count
            transcript.text = result.text

            # 原始响应留档：将来排查「为什么某个说话人被归错」时只能靠它
            transcript.raw_json = json.dumps(result.raw, ensure_ascii=False)[:2_000_000]
            session.add(transcript)
            session.commit()
            session.refresh(transcript)
            transcript_id = transcript.id

            # 重新落句级数据（重跑时先清旧的，避免残留）
            old = session.exec(
                select(TranscriptSegment).where(
                    TranscriptSegment.transcript_id == transcript_id
                )
            ).all()
            for row in old:
                session.delete(row)
            session.commit()

            for seg in result.segments:
                session.add(
                    TranscriptSegment(
                        transcript_id=transcript_id,
                        seq=seg.seq,
                        speaker_raw_id=seg.speaker_raw_id,
                        chunk_index=seg.chunk_index,
                        start_ms=seg.start_ms,
                        end_ms=seg.end_ms,
                        text=seg.text,
                    )
                )
            session.commit()

        return transcript_id

    @staticmethod
    def _cleanup_local_audio(local_path: Path | None) -> None:
        if local_path is None:
            return
        try:
            if local_path.exists():
                size_mb = local_path.stat().st_size / 1e6
                local_path.unlink()
                logger.info("已清理本地临时音频（%.1f MB）：%s", size_mb, local_path.name)
        except OSError as exc:
            # 清理失败不影响主流程，只是占点磁盘
            logger.warning("清理本地临时音频失败：%s", exc)
