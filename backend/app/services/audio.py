"""音频预处理：把上传的音频统一成 16kHz / 16bit / 单声道 WAV。

为什么要统一：百炼的 paraformer-v2 只接受单声道音频，而且不同来源的录音
采样率五花八门（44.1k 的 mp3、48k 的 m4a、8k 的电话录音），先归一化再送转写，
能避免一大类难排查的问题。

ffmpeg 二进制的查找顺序（最后一条是关键，它免去了 winget 安装和管理员权限）：
    1. .env 里的 FFMPEG_PATH
    2. 系统 PATH
    3. imageio-ffmpeg 自带的静态二进制
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import AudioError

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2})\.(\d+)")
_AUDIO_LINE_RE = re.compile(r"Audio:\s*(.+)")
_HZ_RE = re.compile(r"(\d+)\s*Hz")
_CHANNELS_RE = re.compile(r"\b(mono|stereo|(\d+)\s*channels?)\b", re.IGNORECASE)


def ffmpeg_exe() -> str:
    """定位可用的 ffmpeg 可执行文件。"""
    configured = get_settings().ffmpeg_path
    if configured and Path(configured).exists():
        return configured

    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - 取决于本机环境
        raise AudioError(
            "找不到 ffmpeg。请在 .env 里设置 FFMPEG_PATH，或安装 imageio-ffmpeg："
            "pip install imageio-ffmpeg",
            detail=str(exc),
        ) from exc


def has_ffmpeg() -> bool:
    try:
        return bool(ffmpeg_exe())
    except AudioError:
        return False


async def _run(*args: str) -> tuple[int, str]:
    """执行 ffmpeg 并返回 (退出码, stderr 全文)。"""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    return process.returncode or 0, stderr.decode("utf-8", errors="replace")


async def probe(path: Path) -> dict[str, int]:
    """探测音频的时长、采样率、声道数。

    刻意不依赖 ffprobe —— imageio-ffmpeg 只自带 ffmpeg 不带 ffprobe，
    而 `ffmpeg -i file` 本来就会把同样的信息打在 stderr 上。
    """
    if not path.exists():
        raise AudioError(f"音频文件不存在：{path}")

    # ffmpeg 探测时没有指定输出文件会返回非 0 退出码，这是正常的，只看 stderr
    _, stderr = await _run(ffmpeg_exe(), "-hide_banner", "-i", str(path))

    duration_ms = 0
    if match := _DURATION_RE.search(stderr):
        hours, minutes, seconds, frac = match.groups()
        duration_ms = (
            int(hours) * 3_600_000
            + int(minutes) * 60_000
            + int(seconds) * 1000
            + int(frac.ljust(3, "0")[:3])
        )

    sample_rate = 0
    channels = 0
    if audio_line := _AUDIO_LINE_RE.search(stderr):
        line = audio_line.group(1)
        if hz := _HZ_RE.search(line):
            sample_rate = int(hz.group(1))
        if ch := _CHANNELS_RE.search(line):
            token = ch.group(1).lower()
            if token == "mono":
                channels = 1
            elif token == "stereo":
                channels = 2
            elif ch.group(2):
                channels = int(ch.group(2))

    if duration_ms == 0:
        raise AudioError(
            f"无法解析音频信息，可能不是有效的音频文件：{path.name}",
            detail=stderr.strip().splitlines()[-1] if stderr.strip() else None,
        )

    return {
        "duration_ms": duration_ms,
        "sample_rate": sample_rate,
        "channels": channels,
    }


async def to_wav16k_mono(src: Path, dst: Path) -> Path:
    """转成 16kHz / 16bit / 单声道 PCM WAV。

    -vn            丢掉封面图等视频流（很多 m4a 带专辑封面，不丢会报错）
    -ac 1          单声道，百炼的要求
    -ar 16000      16kHz，语音识别的标准输入
    -acodec pcm_s16le  16bit PCM
    """
    dst.parent.mkdir(parents=True, exist_ok=True)

    code, stderr = await _run(
        ffmpeg_exe(),
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(src),
        "-vn",
        "-ac", str(CHANNELS),
        "-ar", str(SAMPLE_RATE),
        "-acodec", "pcm_s16le",
        str(dst),
    )

    if code != 0 or not dst.exists():
        raise AudioError(
            f"音频转码失败：{src.name}",
            detail=stderr.strip()[-500:] if stderr.strip() else None,
        )

    logger.info("转码完成 %s -> %s (%.1f MB)", src.name, dst.name, dst.stat().st_size / 1e6)
    return dst


def estimate_wav_size_bytes(duration_ms: int) -> int:
    """16kHz / 16bit / 单声道 WAV 的体积估算，用于磁盘空间预警。"""
    return int(duration_ms / 1000 * SAMPLE_RATE * 2)
