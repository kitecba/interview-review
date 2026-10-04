"""阿里云 OSS：上传音频、生成签名 URL。

**为什么需要它**：百炼的录音文件识别只接受公网可访问的音频 URL，不支持本地文件直传。
所以流程必然是「先传 OSS → 拿签名 URL → 提交转写任务」。

**签名 URL 的有效期是个坑**：转写任务可能排队，如果 URL 在任务跑完前失效，
转写就会失败 —— 而 ASR 是按音频时长计费的，失败重来就是重复花钱。
所以默认给到 24 小时，配置项是 OSS_SIGNED_URL_TTL_SECONDS。

oss2 是同步库，全部用 asyncio.to_thread 包一层，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import ConfigError, StorageError

logger = logging.getLogger(__name__)

# 上传超时。音频文件最大可到几百 MB，给宽松一点。
_UPLOAD_TIMEOUT_SECONDS = 600


def is_configured() -> bool:
    """OSS 相关配置是否齐全。用于在流水线早期给出明确报错，而不是等上传时才失败。"""
    settings = get_settings()
    return bool(
        settings.oss_endpoint
        and settings.oss_bucket
        and settings.oss_access_key_id
        and settings.oss_access_key_secret
    )


def _bucket():
    """构造 OSS Bucket 客户端。延迟导入 oss2，让没配 OSS 时也能启动服务。"""
    settings = get_settings()
    if not is_configured():
        raise ConfigError(
            "OSS 未配置完整，需要 OSS_ENDPOINT / OSS_BUCKET / "
            "OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET"
        )

    import oss2

    auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
    return oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket)


def build_object_key(interview_id: str, content_hash: str, suffix: str = ".wav") -> str:
    """对象名用内容哈希。

    好处是天然去重：同一个录音重复上传会命中同一个对象，不会产生多份副本。
    """
    return f"interviews/{interview_id}/{content_hash}{suffix}"


def _upload_sync(local_path: Path, oss_key: str) -> str:
    bucket = _bucket()
    try:
        bucket.put_object_from_file(
            oss_key,
            str(local_path),
            # 私有读 + 走签名 URL，避免音频被公网任意访问
            headers={"x-oss-object-acl": "private"},
        )
    except Exception as exc:
        raise StorageError(f"OSS 上传失败：{oss_key}", detail=str(exc)) from exc
    return oss_key


async def upload_audio(local_path: Path, oss_key: str) -> str:
    """把预处理后的 wav 上传到 OSS，返回对象名。"""
    if not local_path.exists():
        raise StorageError(f"待上传文件不存在：{local_path}")

    size_mb = local_path.stat().st_size / 1e6
    logger.info("上传 OSS：%s（%.1f MB）", oss_key, size_mb)

    await asyncio.wait_for(
        asyncio.to_thread(_upload_sync, local_path, oss_key),
        timeout=_UPLOAD_TIMEOUT_SECONDS,
    )
    return oss_key


def _signed_url_sync(oss_key: str, ttl_seconds: int) -> str:
    bucket = _bucket()
    try:
        return bucket.sign_url("GET", oss_key, ttl_seconds, slash_safe=True)
    except Exception as exc:
        raise StorageError(f"生成签名 URL 失败：{oss_key}", detail=str(exc)) from exc


async def signed_url(oss_key: str, ttl_seconds: int | None = None) -> str:
    """生成公网可访问的签名 URL，交给百炼去拉取音频。"""
    settings = get_settings()
    ttl = ttl_seconds or settings.oss_signed_url_ttl_seconds
    url = await asyncio.to_thread(_signed_url_sync, oss_key, ttl)
    # 日志里不能出现完整签名 URL（含 Signature 参数），logging 的脱敏过滤器会打码
    logger.info("生成签名 URL：%s（有效期 %d 秒）", oss_key, ttl)
    return url


def _delete_sync(oss_key: str) -> None:
    _bucket().delete_object(oss_key)


async def delete_object(oss_key: str) -> None:
    """删除 OSS 对象。删除面试记录时调用，失败只记警告不阻断。"""
    try:
        await asyncio.to_thread(_delete_sync, oss_key)
        logger.info("已删除 OSS 对象：%s", oss_key)
    except Exception as exc:
        logger.warning("删除 OSS 对象失败（不影响主流程）：%s — %s", oss_key, exc)


def _probe_sync() -> str:
    """探测 OSS 连通性，返回 bucket 信息。供 check_env / health 使用。"""
    bucket = _bucket()
    info = bucket.get_bucket_info()
    return f"{info.name} @ {info.location}"


async def probe() -> str:
    return await asyncio.to_thread(_probe_sync)
