"""命令行端到端跑一遍流水线。

用法：
    # 用真实录音
    python scripts/smoke_pipeline.py --audio "D:/recordings/interview.mp3"

    # 没有录音时，生成一段合成音频验证管道连通性
    python scripts/smoke_pipeline.py --generate

为什么要有这个脚本：ASR 质量、说话人分离效果、大模型 JSON 输出稳定性这三件事
都没法用假数据验证，但**管道本身**（音频能否解析、任务能否落库、阶段能否断点续跑）
可以。把 UI 放到最后做，就是为了让这些风险最早在命令行里暴露。

合成音频只能验证管道，**不能验证转写质量** —— 它是一段高低音交替的信号，
不含真实语音，ASR 对它不会有有意义的输出。真实效果必须用真人录音测。
"""

from __future__ import annotations

import argparse
import asyncio
import math
import struct
import sys
import wave
from pathlib import Path
from uuid import uuid4

# Windows 控制台默认 GBK，中文会乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from sqlmodel import Session, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.db.engine import get_engine, init_db  # noqa: E402
from app.db.models import AudioAsset, Interview, PipelineRun, PipelineStage  # noqa: E402
from app.pipeline.runner import PipelineRunner  # noqa: E402
from app.pipeline.state import STAGE_LABELS  # noqa: E402
from app.pipeline.stages import register_builtin_stages  # noqa: E402
from app.services.hashing import sha256_file  # noqa: E402


def generate_tone_wav(path: Path, seconds: int = 20) -> Path:
    """生成一段「两人交替说话」的合成音频。

    用两个不同频率的方波交替，模拟说话的轮次切换。片段之间留 0.4 秒静音，
    因为很多语音系统依赖静音做断句。

    再次强调：这只是管道测试用的信号，不含真实语音。
    """
    rate = 16000
    path.parent.mkdir(parents=True, exist_ok=True)

    frames = bytearray()
    turn_seconds = 2.0
    silence_seconds = 0.4
    elapsed = 0.0
    speaker = 0

    while elapsed < seconds:
        freq = 220 if speaker == 0 else 360
        count = int(rate * turn_seconds)
        for i in range(count):
            # 加一点振幅包络，避免方波边缘产生刺耳爆音
            env = 0.5 + 0.5 * math.sin(math.pi * (i / count))
            frames += struct.pack("<h", int(6000 * env * math.sin(2 * math.pi * freq * i / rate)))
        frames += b"\x00\x00" * int(rate * silence_seconds)
        elapsed += turn_seconds + silence_seconds
        speaker = 1 - speaker

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames))

    print(f"  已生成合成音频：{path}（{seconds} 秒）")
    return path


def prepare_interview(audio_path: Path, title: str) -> tuple[str, str, str]:
    """把音频登记进数据库，返回 (interview_id, run_id, audio_sha256)。"""
    settings = get_settings()
    settings.ensure_dirs()

    content_hash = sha256_file(audio_path)
    interview_id = uuid4().hex

    # 按 interview_id 归档原始文件，与上传接口的约定保持一致
    dest_dir = settings.uploads_path / interview_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / audio_path.name

    if audio_path.resolve() != dest.resolve():
        dest.write_bytes(audio_path.read_bytes())

    with Session(get_engine()) as session:
        interview = Interview(id=interview_id, title=title)
        session.add(interview)
        # 模型之间没有定义 relationship，SQLAlchemy 无从得知插入顺序，
        # 必须显式 flush 一次把父表写下去，否则 audio_asset 会撞外键约束。
        session.flush()

        asset = AudioAsset(
            interview_id=interview_id,
            original_filename=audio_path.name,
            local_path=str(dest),
            sha256=content_hash,
            size_bytes=dest.stat().st_size,
        )
        session.add(asset)

        run = PipelineRun(interview_id=interview_id)
        session.add(run)
        session.commit()

        run_id = run.id

    print(f"  面试记录：{interview_id}")
    print(f"  音频指纹：{content_hash[:16]}…")
    return interview_id, run_id, content_hash


async def wait_for_run(run_id: str, timeout_seconds: int = 1800) -> str:
    """轮询等待 run 结束，同时把进度打出来。"""
    last_message = ""
    waited = 0
    while waited < timeout_seconds:
        await asyncio.sleep(1)
        waited += 1

        with Session(get_engine()) as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                return "missing"
            status, message, progress = run.status, run.message or "", run.progress

        if message != last_message:
            print(f"  [{progress:3d}%] {message}")
            last_message = message

        if status in ("done", "failed", "canceled"):
            return status

    return "timeout"


def print_stages(run_id: str) -> None:
    with Session(get_engine()) as session:
        rows = session.exec(
            select(PipelineStage).where(PipelineStage.run_id == run_id)
        ).all()

    if not rows:
        print("  （没有阶段记录）")
        return

    print("\n阶段执行明细：")
    for row in sorted(rows, key=lambda r: r.stage):
        label = STAGE_LABELS.get(row.stage, row.stage)
        mark = {"done": "✓", "failed": "✗", "skipped": "↺", "running": "…"}.get(row.status, "·")
        line = f"  {mark} {label}（{row.stage}）状态={row.status} 尝试={row.attempt}"
        if row.error:
            line += f"\n      错误：{row.error}"
        print(line)


async def main() -> int:
    parser = argparse.ArgumentParser(description="跑一遍流水线")
    parser.add_argument("--audio", type=Path, help="要处理的音频文件")
    parser.add_argument("--generate", action="store_true", help="生成合成音频（管道测试用）")
    parser.add_argument("--title", default="冒烟测试", help="面试标题")
    args = parser.parse_args()

    setup_logging()

    if args.audio is None and not args.generate:
        parser.error("请用 --audio 指定音频文件，或用 --generate 生成合成音频")

    print("\n=== 面试复盘助手 · 流水线冒烟测试 ===\n")

    audio_path = args.audio
    if audio_path is None:
        audio_path = generate_tone_wav(get_settings().data_path / "samples" / "smoke_tone.wav")

    if not audio_path.exists():
        print(f"音频文件不存在：{audio_path}")
        return 2

    init_db()

    print("[1] 登记面试记录")
    interview_id, run_id, _ = prepare_interview(audio_path, args.title)

    runner = PipelineRunner()
    register_builtin_stages(runner)
    await runner.start()

    print("\n[2] 执行流水线")
    await runner.enqueue(run_id)
    status = await wait_for_run(run_id)

    print_stages(run_id)
    print(f"\n[3] 最终状态：{status}")

    await runner.stop()
    return 0 if status == "done" else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
