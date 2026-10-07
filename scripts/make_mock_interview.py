"""合成一段模拟面试对话，用于端到端验证。

用法：
    python scripts/make_mock_interview.py [--out backend/data/samples/mock_interview.wav]

**为什么需要它**：ASR 的转写质量只能拿真实语音来验证 —— 音调信号测不出
「B+树」「索引下推」这类技术名词会不会被转错，也测不出标点和断句。

用 Windows 自带的中文语音（Microsoft Huihui）把一段写好的面试对话念出来，
两个角色用不同的语速区分。

**它的局限要说清楚**：语速不同**不等于**音色不同，所以说话人分离（diarization）
在这段音频上未必能正确分成两个人。它能验证的是：转写准确性、接口调用、
结果解析、以及整个流水线。要验证说话人分离的真实效果，必须用真人录音。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 模拟一场后端岗面试。刻意包含技术名词和数字，用来检验转写准确率。
DIALOGUE: list[tuple[str, str]] = [
    ("面试官", "你好，请先做一个自我介绍。"),
    ("候选人", "好的。我是张伟，有五年后端开发经验，主要做 Java 和分布式系统，"
              "最近两年在负责一个高并发的交易系统。"),
    ("面试官", "你提到分布式系统，能讲一下你对 CAP 理论的理解吗？"),
    ("候选人", "CAP 指的是一致性、可用性和分区容错性，这三者不可能同时满足。"
              "因为网络分区在分布式系统里是必然发生的，所以实际上是在一致性和可用性之间做取舍。"),
    ("面试官", "那你们项目里是怎么选的？"),
    ("候选人", "我们的订单系统选择了 CP，因为订单数据不能出现不一致。"
              "底层用的是 Raft 协议来保证强一致性。"),
    ("面试官", "如果线上发现数据库索引失效，你会怎么排查？"),
    ("候选人", "我会先用 explain 看执行计划，重点看 type 和 key 这两列。"
              "然后检查是不是对索引列做了函数运算，或者发生了类型隐式转换导致索引失效。"),
    ("面试官", "那你了解 B 加树和哈希索引的区别吗？"),
    ("候选人", "B 加树支持范围查询和最左前缀匹配，所以更适合范围扫描；"
              "哈希索引只能做等值查询，不支持排序和范围查找。"),
    ("面试官", "好的，你有什么想问我的吗？"),
    ("候选人", "我想了解一下团队目前的技术栈，以及未来一年的技术规划。"),
]

# 两个角色用不同语速。系统里只有一个中文音色，靠语速拉开差异。
SPEAKER_RATE = {"面试官": -2, "候选人": 2}

_PS_TEMPLATE = """
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('Microsoft Huihui Desktop')
$s.Rate = {rate}
$s.SetOutputToWaveFile('{out}')
$s.Speak('{text}')
$s.Dispose()
"""


def _ps_escape(text: str) -> str:
    # PowerShell 单引号字符串里，单引号用两个单引号转义
    return text.replace("'", "''")


def synthesize_turn(text: str, rate: int, out_path: Path) -> None:
    script = _PS_TEMPLATE.format(
        rate=rate, out=str(out_path).replace("\\", "\\\\"), text=_ps_escape(text)
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".ps1", delete=False, encoding="utf-8-sig"
    ) as fh:
        fh.write(script)
        script_path = fh.name

    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0 or not out_path.exists():
            raise RuntimeError(
                f"语音合成失败：{result.stderr.strip() or result.stdout.strip()}"
            )
    finally:
        Path(script_path).unlink(missing_ok=True)


def concat_wavs(parts: list[Path], out_path: Path) -> None:
    """用 ffmpeg 把各轮对话按顺序拼成一条音轨。"""
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    inputs: list[str] = []
    for part in parts:
        inputs.extend(["-i", str(part)])

    # 各段采样率可能不一致，concat 过滤器要求统一，这里先全部重采样
    filter_parts = "".join(
        f"[{i}:a]aresample=16000,aformat=sample_fmts=s16:channel_layouts=mono[a{i}];"
        for i in range(len(parts))
    )
    concat_inputs = "".join(f"[a{i}]" for i in range(len(parts)))
    filter_complex = (
        f"{filter_parts}{concat_inputs}concat=n={len(parts)}:v=0:a=1[out]"
    )

    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ar", "16000", "-ac", "1",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 拼接失败：{result.stderr.strip()[-500:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="合成模拟面试音频")
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "backend" / "data" / "samples" / "mock_interview.wav",
    )
    args = parser.parse_args()

    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n=== 合成模拟面试对话（{len(DIALOGUE)} 轮）===\n")

    tmp_dir = Path(tempfile.mkdtemp(prefix="mock_interview_"))
    parts: list[Path] = []
    try:
        for index, (speaker, text) in enumerate(DIALOGUE):
            part = tmp_dir / f"turn_{index:02d}.wav"
            rate = SPEAKER_RATE.get(speaker, 0)
            synthesize_turn(text, rate, part)
            parts.append(part)
            preview = text[:22] + ("…" if len(text) > 22 else "")
            print(f"  [{index + 1:2d}/{len(DIALOGUE)}] {speaker}（语速 {rate:+d}）：{preview}")

        print("\n正在拼接音轨…")
        concat_wavs(parts, out_path)

        size_mb = out_path.stat().st_size / 1e6
        print(f"\n完成：{out_path}")
        print(f"  体积 {size_mb:.1f} MB（16kHz 单声道 WAV ≈ 每分钟 1.9 MB）")
        print(f"  预估时长 {size_mb / 1.9:.1f} 分钟\n")
        print("提醒：语速差异不等于音色差异，说话人分离在这段音频上未必能分成两人。")
        print("     转写准确性和接口链路可以验证，分离效果必须用真人录音。\n")
        return 0
    finally:
        for part in parts:
            part.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
