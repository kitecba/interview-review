"""DeepSeek 客户端。

三个必须绕开的坑（都是调研阶段查证的，不是猜的）：

1. **思考模式默认开启，且开启时不接受 temperature / top_p /
   presence_penalty / frequency_penalty**。传了会报错，所以这里在
   thinking=True 时直接不构造这些参数。

2. **/v1 端点拒绝 tool_choice="required"**，所以本项目的 JSON 输出一律走
   response_format={"type": "json_object"}，不用 function calling。
   另外 JSON 模式要求 prompt 里必须出现 "json" 字样，否则模型可能返回空内容 ——
   这个约束由 prompts 层保证，客户端这边兜底检查一次。

3. **多轮时上一轮的 reasoning_content 必须回传**，不传会 400。
   这里用 dataclass 把 (assistant 消息 + reasoning_content) 成对保存，
   修复调用时原样带回。

成本估算用的是峰时价（输入 $0.30/M、输出 $1.20/M），实际闲时是半价，
所以界面显示的是上界估算。缓存命中会便宜得多，这里不做区分。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import ConfigError, LlmError, LlmJsonError

logger = logging.getLogger(__name__)

# 峰时单价（美元 / 百万 token）。用于界面上的成本展示，是估算不是账单。
_PRICE_INPUT_PER_M = 0.30
_PRICE_OUTPUT_PER_M = 1.20

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.5


@dataclass
class LlmResult:
    """一次调用的结果。"""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    # 思考模式下模型返回的推理内容。多轮时必须回传，否则 400。
    reasoning_content: str | None = None

    @property
    def cost_estimate(self) -> float:
        return (
            self.prompt_tokens / 1_000_000 * _PRICE_INPUT_PER_M
            + self.completion_tokens / 1_000_000 * _PRICE_OUTPUT_PER_M
        )


@dataclass
class _Message:
    """一条对话消息，含 reasoning_content 以便多轮回传。"""

    role: str
    content: str
    reasoning_content: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.reasoning_content:
            payload["reasoning_content"] = self.reasoning_content
        payload.update(self.extra)
        return payload


class DeepSeekClient:
    """DeepSeek 官方 API 的异步客户端。"""

    def __init__(self, *, timeout: float = 300.0) -> None:
        settings = get_settings()
        if not settings.deepseek_api_key:
            raise ConfigError("缺少 DEEPSEEK_API_KEY，请在 .env 中配置")

        self._api_key = settings.deepseek_api_key
        self._base_url = settings.deepseek_base_url.rstrip("/")
        self._model = settings.deepseek_model
        self._timeout = timeout

    @property
    def model(self) -> str:
        return self._model

    def _chat_url(self) -> str:
        # 官方给的是 https://api.deepseek.com，OpenAI 兼容路径可以直接拼 /chat/completions；
        # 如果用户填了带 /v1 的中转地址，就不要重复拼。
        if self._base_url.endswith("/v1"):
            return f"{self._base_url}/chat/completions"
        return f"{self._base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def list_models(self) -> list[str]:
        """拉取可用模型列表，用于探针脚本验证模型名是否有效。"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self._base_url}/models", headers=self._headers()
            )
            response.raise_for_status()
            payload = response.json()
        return [item.get("id", "") for item in payload.get("data", [])]

    async def chat(
        self,
        messages: list[_Message] | list[dict[str, Any]],
        *,
        thinking: bool = True,
        temperature: float | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> LlmResult:
        """发一次对话请求，带指数退避重试。

        thinking=True 时忽略 temperature —— 思考模式不接受采样参数，
        传了会被服务端拒绝。
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                m.to_payload() if isinstance(m, _Message) else m for m in messages
            ],
        }

        if thinking:
            # 思考模式的开关字段；不需要额外设置 sampling 参数
            payload["enable_thinking"] = True
            if temperature is not None:
                logger.debug("思考模式已开启，忽略传入的 temperature")
        else:
            payload["enable_thinking"] = False
            if temperature is not None:
                payload["temperature"] = temperature

        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if extra_body:
            payload.update(extra_body)

        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self._chat_url(), headers=self._headers(), json=payload
                    )

                latency_ms = int((time.perf_counter() - started) * 1000)

                if response.status_code == 429 or response.status_code >= 500:
                    raise LlmError(
                        f"DeepSeek 返回 {response.status_code}",
                        detail=response.text[:300],
                    )

                if response.status_code >= 400:
                    # 4xx 多为请求本身的问题，重试无用，直接抛出
                    raise LlmError(
                        f"DeepSeek 请求被拒绝（{response.status_code}）",
                        detail=response.text[:500],
                    )

                data = response.json()
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                usage = data.get("usage") or {}

                return LlmResult(
                    content=message.get("content") or "",
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    latency_ms=latency_ms,
                    reasoning_content=message.get("reasoning_content"),
                )

            except LlmError as exc:
                last_error = exc
                if not exc.retryable or attempt == _MAX_RETRIES:
                    raise
            except httpx.HTTPError as exc:
                last_error = LlmError(f"DeepSeek 网络错误：{exc}")
                if attempt == _MAX_RETRIES:
                    raise last_error from exc

            delay = _BACKOFF_BASE**attempt
            logger.warning(
                "DeepSeek 调用失败（第 %d 次），%.1fs 后重试：%s",
                attempt,
                delay,
                last_error,
            )
            await asyncio.sleep(delay)

        raise last_error or LlmError("DeepSeek 调用失败")

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        thinking: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
        repair_on_invalid: bool = True,
    ) -> tuple[dict[str, Any], LlmResult]:
        """要求模型返回 JSON 并解析成 dict。

        解析失败时做一次修复调用 —— 把错误信息连同原回答一起发回去让模型自己改，
        这比直接重试整轮便宜得多。
        """
        if "json" not in system_prompt.lower() and "json" not in user_prompt.lower():
            # JSON 模式要求 prompt 里出现 json 字样，否则模型可能返回空内容
            system_prompt = system_prompt + "\n\n请以 json 格式输出。"

        messages = [
            _Message(role="system", content=system_prompt),
            _Message(role="user", content=user_prompt),
        ]

        result = await self.chat(
            messages,
            thinking=thinking,
            temperature=temperature,
            json_mode=True,
            max_tokens=max_tokens,
        )

        try:
            return _parse_json(result.content), result
        except LlmJsonError as first_error:
            if not repair_on_invalid:
                raise

            logger.warning("模型返回的不是合法 JSON，发起一次修复调用")
            repair_messages = [
                *messages,
                # 把上一轮的推理内容一并带回，否则思考模式下会 400
                _Message(
                    role="assistant",
                    content=result.content,
                    reasoning_content=result.reasoning_content,
                ),
                _Message(
                    role="user",
                    content=(
                        f"你上一次的输出无法被解析为 json，错误是：{first_error.message}\n"
                        "请只输出一个合法的 json 对象，不要包含 markdown 代码块标记，"
                        "不要有任何解释性文字。"
                    ),
                ),
            ]

            repair_result = await self.chat(
                repair_messages,
                thinking=thinking,
                temperature=temperature,
                json_mode=True,
                max_tokens=max_tokens,
            )

            try:
                return _parse_json(repair_result.content), repair_result
            except LlmJsonError:
                raise LlmJsonError(
                    "模型连续两次返回的内容都无法解析为 JSON",
                    detail=repair_result.content[:500],
                ) from first_error


def _parse_json(content: str) -> dict[str, Any]:
    """解析模型返回的 JSON。

    即便开了 JSON 模式，模型偶尔仍会裹一层 markdown 代码块，这里一并剥掉。
    """
    import json

    text = (content or "").strip()
    if not text:
        raise LlmJsonError("模型返回了空内容")

    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # 去掉 ```json 那行
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        # 兜底：从第一个 { 截到最后一个 }，应对模型在 JSON 前后加了说明文字的情况
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                raise LlmJsonError("模型输出不是合法 JSON", detail=text[:300]) from exc
        else:
            raise LlmJsonError("模型输出不是合法 JSON", detail=text[:300]) from exc

    if not isinstance(parsed, dict):
        raise LlmJsonError("模型输出的 JSON 顶层不是对象")

    return parsed
