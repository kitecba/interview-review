"""内容指纹与幂等键计算。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_CHUNK_SIZE = 1024 * 1024  # 1 MiB，避免把大音频一次性读进内存


def sha256_file(path: Path) -> str:
    """计算文件内容的 sha256。

    这是最重要的一处省钱逻辑：同一个录音文件重复上传时，
    凭借这个指纹直接复用已有的转写结果，不再调一次付费 ASR。
    """
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stage_input_hash(*parts: Any) -> str:
    """计算某个阶段的幂等键。

    参与计算的有：上游产物、prompt 版本、模型名。
    这样改了 prompt 就会改变 hash，从而自动触发该阶段重跑 ——
    避免出现「改了提示词但结果没变」这种最难排查的问题。
    """
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, (dict, list)):
            payload = json.dumps(part, ensure_ascii=False, sort_keys=True)
        else:
            payload = str(part)
        digest.update(payload.encode("utf-8"))
        digest.update(b"\x00")  # 分隔符，防止不同分段拼接后碰撞
    return digest.hexdigest()
