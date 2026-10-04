"""探针：实测 DeepSeek 官方 API 的真实行为。

用法：
    python scripts/probe_deepseek_audio.py

**为什么需要这个脚本**：调研阶段关于「deepseek-flash 收不收音频输入」得到了
互相矛盾的结论 —— 官方 Responses API 的内容块列表里只有 input_text / input_image /
reasoning_text，没有 input_audio；但 2026 年 9 月确实有过一个原生多模态内测版本
（模型 ID 带 expires-on-0910 后缀，已过期）。与其继续靠检索结果猜，不如花几分钱实测一次。

同时验证另外三件影响架构的事：
  1. 配置的模型名在 /models 列表里是否存在
  2. JSON 输出模式是否稳定返回可解析的 JSON
  3. 思考模式下传 temperature 是否真的被拒绝

结论会被写进 docs/probes.md。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import sys
import wave
from pathlib import Path

# Windows 控制台默认按 GBK 输出，中文会变成乱码。强制切到 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import httpx  # noqa: E402

from app.core.config import get_settings  # noqa: E402


def make_tiny_wav(seconds: float = 1.0, sample_rate: int = 16000) -> bytes:
    """现场合成一段极短的 WAV，用于测试音频输入。

    不依赖 ffmpeg，也不读用户的真实录音 —— 探针只关心「服务端收不收这种内容块」，
    内容是什么无所谓。
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = b"".join(
            struct.pack("<h", int(3000 * __import__("math").sin(i / 20)))
            for i in range(int(sample_rate * seconds))
        )
        wav.writeframes(frames)
    return buffer.getvalue()


class Probe:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.deepseek_api_key
        self.base = settings.deepseek_base_url.rstrip("/")
        self.model = settings.deepseek_model
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.results: list[tuple[str, str, str]] = []

    def record(self, name: str, verdict: str, detail: str) -> None:
        self.results.append((name, verdict, detail))
        icon = {"支持": "[OK]", "不支持": "[!!]", "异常": "[!!]", "可用": "[OK]"}.get(verdict, "[--]")
        print(f"\n{icon} {name}: {verdict}")
        print(f"    {detail}")

    async def run(self) -> int:
        if not self.api_key:
            print("[!!] 未配置 DEEPSEEK_API_KEY，无法探测。请在 .env 中填写后重试。")
            return 2

        print("=" * 68)
        print("DeepSeek 官方 API 行为探针")
        print(f"  base_url : {self.base}")
        print(f"  model    : {self.model}")
        print("=" * 68)

        async with httpx.AsyncClient(timeout=120.0) as client:
            await self.probe_models(client)
            await self.probe_plain_json(client)
            await self.probe_temperature_in_thinking(client)
            await self.probe_audio_input(client)

        self.summarize()
        return 0

    # ------------------------------------------------------------------
    async def probe_models(self, client: httpx.AsyncClient) -> None:
        try:
            response = await client.get(f"{self.base}/models", headers=self.headers)
            if response.status_code != 200:
                self.record("模型列表", "异常", f"HTTP {response.status_code}: {response.text[:200]}")
                return
            models = [m.get("id") for m in response.json().get("data", [])]
            if self.model in models:
                self.record("模型列表", "可用", f"{self.model} 存在。全部：{models}")
            else:
                self.record(
                    "模型列表",
                    "异常",
                    f"配置的 {self.model} 不在列表中！返回：{models}",
                )
        except Exception as exc:
            self.record("模型列表", "异常", repr(exc))

    # ------------------------------------------------------------------
    async def probe_plain_json(self, client: httpx.AsyncClient) -> None:
        """验证 JSON 输出模式稳定可用 —— 整条复盘链都依赖它。"""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你只输出 json。"},
                {
                    "role": "user",
                    "content": '返回一个 json 对象，包含字段 ok（true）和 note（字符串）。',
                },
            ],
            "response_format": {"type": "json_object"},
            "enable_thinking": False,
        }
        try:
            response = await client.post(
                f"{self.base}/chat/completions", headers=self.headers, json=payload
            )
            if response.status_code != 200:
                self.record("JSON 输出模式", "异常", f"HTTP {response.status_code}: {response.text[:300]}")
                return
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            self.record("JSON 输出模式", "可用", f"解析成功：{parsed}")
        except Exception as exc:
            self.record("JSON 输出模式", "异常", repr(exc))

    # ------------------------------------------------------------------
    async def probe_temperature_in_thinking(self, client: httpx.AsyncClient) -> None:
        """思考模式 + temperature：验证是否真的被拒绝。

        这条决定 prompts 层要不要在思考模式下禁用采样参数。
        """
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "说“好”"}],
            "enable_thinking": True,
            "temperature": 0.7,
            "max_tokens": 16,
        }
        try:
            response = await client.post(
                f"{self.base}/chat/completions", headers=self.headers, json=payload
            )
            if response.status_code == 200:
                self.record(
                    "思考模式 + temperature",
                    "可用",
                    "服务端接受该组合（不会报错），但按官方文档该参数会被忽略。",
                )
            else:
                body = response.text[:300]
                verdict = "不支持" if response.status_code == 400 else "异常"
                self.record(
                    "思考模式 + temperature",
                    verdict,
                    f"HTTP {response.status_code}: {body}",
                )
        except Exception as exc:
            self.record("思考模式 + temperature", "异常", repr(exc))

    # ------------------------------------------------------------------
    async def probe_audio_input(self, client: httpx.AsyncClient) -> None:
        """核心问题：官方 API 收不收音频内容块？

        依次尝试三种常见的音频内容块写法，任何一种被接受就说明支持。
        服务端返回的错误码本身就是答案。
        """
        wav_b64 = base64.b64encode(make_tiny_wav()).decode()
        print(f"\n  （测试音频：1 秒 16kHz 单声道 WAV，base64 后 {len(wav_b64)} 字符）")

        variants = {
            "OpenAI input_audio 格式": {
                "type": "input_audio",
                "input_audio": {"data": wav_b64, "format": "wav"},
            },
            "audio_url 格式": {"type": "audio_url", "audio_url": {"url": f"data:audio/wav;base64,{wav_b64}"}},
            "裸 audio 格式": {"type": "audio", "audio": {"data": wav_b64, "format": "wav"}},
        }

        for name, block in variants.items():
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            block,
                            {"type": "text", "text": "这段音频里说了什么？"},
                        ],
                    }
                ],
                "max_tokens": 64,
                "enable_thinking": False,
            }
            try:
                response = await client.post(
                    f"{self.base}/chat/completions", headers=self.headers, json=payload
                )
                body = response.text[:400]
                if response.status_code == 200:
                    self.record(
                        f"音频输入 · {name}",
                        "支持",
                        f"HTTP 200。模型回答：{body[:200]}",
                    )
                    return
                keyword = "any of the supported" if "supported" in body else ""
                self.record(
                    f"音频输入 · {name}",
                    "不支持",
                    f"HTTP {response.status_code}: {body}{keyword}",
                )
            except Exception as exc:
                self.record(f"音频输入 · {name}", "异常", repr(exc))

    # ------------------------------------------------------------------
    def summarize(self) -> None:
        print("\n" + "=" * 68)
        print("结论")
        print("=" * 68)
        audio = [r for r in self.results if r[0].startswith("音频输入")]
        supported = any(r[1] == "支持" for r in audio)
        if supported:
            print("  [OK] 官方 API 接受音频输入 —— 架构上可以考虑把音频直接送给模型。")
            print("       但注意：90 分钟录音远超单请求体积上限，且模型不做说话人分离，")
            print("       转写这一步仍然需要保留。")
        else:
            print("  [!!] 官方 API 不接受音频输入 —— 与方案的假设一致：")
            print("       音频必须先经 ASR 转成文本，再送给 DeepSeek 生成复盘。")
        print("\n  完整结果已打印在上方，请抄录到 docs/probes.md。\n")


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(Probe().run()))
    except KeyboardInterrupt:
        sys.exit(130)
