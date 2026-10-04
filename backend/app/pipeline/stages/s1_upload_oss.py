"""s1 · 上传音频到 OSS。

**为什么必须有这一步**：百炼的录音文件识别只接受公网可访问的音频 URL，
不支持本地文件直传。所以「先传 OSS、再拿签名 URL」是硬性前置，不是可选的优化。

对象名用内容哈希（`interviews/{id}/{sha256}.wav`），天然去重 —— 同一个录音
重复上传会命中同一个对象，不会产生多份副本，也就不会重复占用存储。

这一阶段**不生成签名 URL**。URL 会过期，而转写可能排队很久；把 URL 的生成
推迟到 s2 真正要用的时候，才能保证它在任务提交那一刻是有效的。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.errors import StorageError
from app.db.engine import get_engine
from app.db.models import AudioAsset
from app.pipeline.stages.base import Stage, StageContext, StageError
from app.services import storage_oss

logger = logging.getLogger(__name__)


class UploadOssStage(Stage):
    name = "s1_upload_oss"
    label = "上传音频"

    async def compute_input_hash(self, ctx: StageContext) -> str:
        import hashlib

        with Session(get_engine()) as session:
            asset = session.exec(
                select(AudioAsset).where(AudioAsset.interview_id == ctx.interview_id)
            ).first()
        if asset is None:
            raise StageError("找不到该面试的音频记录")

        # 音频内容决定上传结果。换一个文件就该重新上传。
        return hashlib.sha256(
            f"{asset.sha256}|{get_settings().oss_bucket}".encode()
        ).hexdigest()

    async def execute(self, ctx: StageContext) -> dict[str, Any]:
        if not storage_oss.is_configured():
            raise StageError(
                "OSS 未配置完整，无法上传音频。需要 OSS_ENDPOINT / OSS_BUCKET / "
                "OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET",
                retryable=False,
            )

        with Session(get_engine()) as session:
            asset = session.exec(
                select(AudioAsset).where(AudioAsset.interview_id == ctx.interview_id)
            ).first()
            if asset is None:
                raise StageError("找不到该面试的音频记录")

            content_hash = asset.sha256
            local_path = Path(asset.local_path) if asset.local_path else None

        if local_path is None or not local_path.exists():
            # 预处理后的 wav 会被删除以节省磁盘空间。如果它不在了，
            # 说明 s0 需要重跑（而不是这里报一个看不懂的错）。
            raise StageError(
                f"预处理后的音频文件已不存在：{local_path or '(未记录)'}。"
                "这通常是因为转写完成后清理了临时文件，需要重跑音频预处理阶段。"
            )

        oss_key = storage_oss.build_object_key(ctx.interview_id, content_hash)
        ctx.emit("正在上传音频到 OSS…", 12)

        try:
            await storage_oss.upload_audio(local_path, oss_key)
        except StorageError as exc:
            # 网络问题可重试；权限问题重试无用
            retryable = "AccessDenied" not in str(exc.detail or "")
            raise StageError(str(exc), retryable=retryable) from exc

        with Session(get_engine()) as session:
            asset = session.exec(
                select(AudioAsset).where(AudioAsset.interview_id == ctx.interview_id)
            ).first()
            if asset:
                asset.oss_key = oss_key
                session.add(asset)
                session.commit()

        ctx.emit("音频已上传", 18)
        logger.info("OSS 上传完成：%s", oss_key)

        return {
            "oss_key": oss_key,
            "size_bytes": local_path.stat().st_size,
            "bucket": get_settings().oss_bucket,
        }
