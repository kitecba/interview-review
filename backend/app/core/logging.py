"""日志配置。

带一层脱敏过滤器：任何写进日志的密钥、OSS 签名 URL 都会被打码。
面试录音的转写文本可能包含隐私，日志里只记长度和摘要，不记全文。
"""

from __future__ import annotations

import logging
import re
import sys

# 常见密钥形态：DeepSeek/OpenAI 的 sk-*、阿里云 AK 的 LTAI* / AKID*
_SECRET_PATTERNS = (
    re.compile(r"(sk-[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]+"),
    re.compile(r"(AKID[A-Za-z0-9]{4})[A-Za-z0-9]+"),
    re.compile(r"(LTAI[A-Za-z0-9]{4})[A-Za-z0-9]+"),
    # OSS 签名 URL 的查询参数
    re.compile(r"(Signature=)[^&\s]+", re.IGNORECASE),
    re.compile(r"(OSSAccessKeyId=)[^&\s]+", re.IGNORECASE),
    re.compile(r"(SecurityToken=)[^&\s]+", re.IGNORECASE),
)


def mask(text: str) -> str:
    """把字符串里的疑似密钥替换为前几位 + ***。"""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1***", text)
    return text


class _MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: mask(v) if isinstance(v, str) else v for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    mask(a) if isinstance(a, str) else a for a in record.args
                )
        return True


def setup_logging(level: int = logging.INFO) -> None:
    # Windows 控制台默认按 GBK 编码输出，日志里的中文会变成乱码。
    # 必须在创建 handler 之前切换，否则 handler 会抓住旧的编码。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    handler.addFilter(_MaskingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn 自带 handler，清掉并改为向上冒泡，避免同一行日志打两遍
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
