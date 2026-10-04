"""s0 · 音频预处理。

把上传的原始音频统一转成 16kHz / 16bit / 单声道 WAV，并探测时长。

为什么必须归一化：百炼的 paraformer-v2 **只接受单声道**，而实际录音的采样率
五花八门（44.1k 的 mp3、48k 的 m4a、8k 的电话录音），先转成统一格式能避开
一大类难排查的问题。

产物里带上音频指纹，下游阶段把它算进幂等键 —— 换了音频，下游自动重跑。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.errors import AudioError
from app.db.engine import get_engine
from app.db.models import AudioAsset, Interview
from app.pipeline.stages.base import Stage, StageContext, StageError
from app.pipeline.state import InterviewStatus
from app.services import audio as audio_service

logger = logging.getLogger(__name__)


class PreprocessStage(Stage):
    name = "s0_preprocess"
    label = "音频预处理"

    async def compute_input_hash(self, ctx: StageContext) -> str:
        import hashlib

        with Session(get_engine()) as session:
            asset = session.exec(
                select(AudioAsset).where(AudioAsset.interview_id == ctx.interview_id)
            ).first()
        if asset is None:
            raise StageError("找不到该面试的音频记录")

        # 原始文件的内容指纹 + 目标格式参数，共同决定这一步的结果
        return hashlib.sha256(
            f"{asset.sha256}|{audio_service.SAMPLE_RATE}|{audio_service.CHANNELS}".encode()
        ).hexdigest()

    async def execute(self, ctx: StageContext) -> dict[str, Any]:
        settings = get_settings()

        with Session(get_engine()) as session:
            interview = session.get(Interview, ctx.interview_id)
            if interview is None:
                raise StageError("面试记录不存在")

            asset = session.exec(
                select(AudioAsset).where(AudioAsset.interview_id == ctx.interview_id)
            ).first()
            if asset is None:
                raise StageError("找不到该面试的音频记录")

            # 原始文件由上传接口按 interview_id 存进 uploads 目录
            source = Path(asset.local_path) if asset.local_path else None
            if source is None or not source.exists():
                candidates = list((settings.uploads_path / ctx.interview_id).glob("*"))
                if not candidates:
                    raise StageError(
                        f"找不到已上传的音频文件：{settings.uploads_path / ctx.interview_id}"
                    )
                source = candidates[0]

            interview.status = InterviewStatus.PROCESSING
            session.add(interview)
            session.commit()

            target = settings.tmp_audio_path / f"{ctx.interview_id}.wav"
            original_name = asset.original_filename
            original_sha = asset.sha256

        ctx.emit(f"正在转换音频格式（{original_name}）…", 3)

        try:
            info = await audio_service.probe(source)
            await audio_service.to_wav16k_mono(source, target)
        except AudioError as exc:
            # ffmpeg 失败多为文件被占用/进程被杀，重试有意义
            raise StageError(str(exc), retryable=True) from exc

        if ctx.is_canceled():
            raise StageError("任务已取消")

        size_bytes = target.stat().st_size
        duration_ms = info["duration_ms"]

        # D 盘空间紧张，转写结束会删掉这个 wav，所以这里提示一下体积
        logger.info(
            "预处理完成：时长 %.1f 分钟，wav %.1f MB（原始采样率 %d Hz / %d 声道）",
            duration_ms / 60000,
            size_bytes / 1e6,
            info["sample_rate"],
            info["channels"],
        )

        with Session(get_engine()) as session:
            asset = session.exec(
                select(AudioAsset).where(AudioAsset.interview_id == ctx.interview_id)
            ).first()
            if asset:
                asset.local_path = str(target)
                asset.duration_ms = duration_ms
                asset.sample_rate = info["sample_rate"]
                asset.channels = info["channels"]
                asset.size_bytes = size_bytes
                session.add(asset)
                session.commit()

        ctx.emit(
            f"音频就绪：{duration_ms / 60000:.1f} 分钟，{size_bytes / 1e6:.1f} MB", 10
        )

        return {
            "duration_ms": duration_ms,
            "size_bytes": size_bytes,
            "source_sample_rate": info["sample_rate"],
            "source_channels": info["channels"],
            "sha256": original_sha,
        }
