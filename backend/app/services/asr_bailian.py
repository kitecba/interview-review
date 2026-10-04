"""阿里云百炼 paraformer-v2 录音文件识别。

**两个必须绕开的坑**（都是调研阶段查证的具体结论，不是泛泛的谨慎）：

1. **不要用 `qwen3-asr-flash-filetrans`**。它名字最像"最新款"，但**不支持说话人分离**。
   百炼文档里明确列出支持 diarization 的只有 `paraformer-v2`、`fun-asr` 和
   `qwen-audio-3.0-asr-flash-filetrans`。默认配置就是 paraformer-v2，别改错。

2. **task_id 一经返回必须立刻持久化**。百炼按音频时长计费，重复提交就是重复花钱。
   所以 submit 和 poll 是分开的两个方法 —— 调用方在 submit 成功后先落库，
   之后无论怎么重试都只走 poll。

3. **只支持单声道**，所以预处理阶段强制转成 16kHz/单声道。

接口形态是「提交异步任务 → 轮询任务状态 → 再从返回的 URL 拉取结果」三步。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import AsrError, AsrTimeoutError, ConfigError

logger = logging.getLogger(__name__)

# 轮询节奏：起步 5 秒，逐步退避到 30 秒。转写通常 1–5 分钟出结果。
_POLL_INITIAL_SECONDS = 5
_POLL_MAX_SECONDS = 30

# 提交接口的固定路径
_SUBMIT_PATH = "/api/v1/services/audio/asr/transcription"
_TASK_PATH = "/api/v1/tasks/{task_id}"

_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}


@dataclass
class AsrSegment:
    """一句转写结果。"""

    seq: int
    speaker_raw_id: int
    start_ms: int
    end_ms: int
    text: str
    # 切片转写时编号只在块内有效，用这个字段区分
    chunk_index: int = 0


@dataclass
class AsrResult:
    task_id: str
    text: str
    segments: list[AsrSegment] = field(default_factory=list)
    duration_ms: int = 0
    speaker_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def speaker_ids(self) -> list[int]:
        return sorted({seg.speaker_raw_id for seg in self.segments})


class BailianAsrClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.dashscope_api_key:
            raise ConfigError("缺少 DASHSCOPE_API_KEY，请在 .env 中配置")

        self._api_key = settings.dashscope_api_key
        self._base = settings.bailian_base_url.rstrip("/")
        self._model = settings.bailian_asr_model

    @property
    def model(self) -> str:
        return self._model

    def _headers(self, *, async_submit: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if async_submit:
            # 录音文件识别是异步任务，必须带这个头
            headers["X-DashScope-Async"] = "enable"
        return headers

    # ------------------------------------------------------------------
    async def submit(
        self,
        file_url: str,
        *,
        diarization: bool = True,
        speaker_count: int | None = 2,
        channel_id: list[int] | None = None,
    ) -> str:
        """提交转写任务，返回 task_id。

        **调用方拿到这个 id 后必须立刻落库** —— 它是避免重复计费的唯一凭据。
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "input": {"file_urls": [file_url]},
            "parameters": {},
        }

        if diarization:
            payload["parameters"]["diarization_enabled"] = True
            if speaker_count:
                # 官方说明这是个人数提示，不保证结果一致，但实测能显著改善分离效果
                payload["parameters"]["speaker_count"] = speaker_count
        if channel_id:
            payload["parameters"]["channel_id"] = channel_id

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self._base}{_SUBMIT_PATH}",
                    headers=self._headers(async_submit=True),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise AsrError(f"提交转写任务失败（网络错误）：{exc}") from exc

        if response.status_code >= 400:
            raise AsrError(
                f"提交转写任务被拒绝（HTTP {response.status_code}）",
                detail=response.text[:500],
            )

        data = response.json()
        task_id = (data.get("output") or {}).get("task_id")
        if not task_id:
            raise AsrError("提交转写任务后未返回 task_id", detail=str(data)[:500])

        logger.info("转写任务已提交：task_id=%s model=%s", task_id, self._model)
        return task_id

    # ------------------------------------------------------------------
    async def poll_once(self, task_id: str) -> dict[str, Any]:
        """查询一次任务状态。"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(
                    f"{self._base}{_TASK_PATH.format(task_id=task_id)}",
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            # 网络抖动不算任务失败，交给轮询循环重试
            raise AsrError(f"查询转写任务失败（网络错误）：{exc}") from exc

        if response.status_code >= 400:
            raise AsrError(
                f"查询转写任务被拒绝（HTTP {response.status_code}）",
                detail=response.text[:500],
            )
        return response.json()

    async def poll_until_done(
        self,
        task_id: str,
        *,
        duration_ms: int = 0,
        on_tick=None,
    ) -> dict[str, Any]:
        """轮询直到任务结束。

        硬超时设成 max(15 分钟, 音频时长 ×2) —— 一小时录音的转写通常几分钟完成，
        但排队高峰可能更久，给足余量。超时抛 AsrTimeoutError（可重试），
        **调用方应重新调用本方法继续轮询，而不是重新 submit**。
        """
        timeout_seconds = max(900, int(duration_ms / 1000 * 2))
        waited = 0
        delay = _POLL_INITIAL_SECONDS

        while waited < timeout_seconds:
            await asyncio.sleep(delay)
            waited += delay

            data = await self.poll_once(task_id)
            output = data.get("output") or {}
            status = output.get("task_status")

            if on_tick:
                on_tick(status, waited)

            if status == "SUCCEEDED":
                logger.info("转写任务完成：%s（耗时约 %d 秒）", task_id, waited)
                return data

            if status in _TERMINAL_STATUSES:
                message = output.get("message") or output.get("code") or "未知原因"
                raise AsrError(f"转写任务失败（{status}）：{message}", detail=str(output)[:500])

            logger.debug("转写进行中：%s 状态=%s 已等待 %d 秒", task_id, status, waited)
            delay = min(delay + 5, _POLL_MAX_SECONDS)

        raise AsrTimeoutError(
            f"转写任务轮询超时（已等待 {timeout_seconds} 秒）",
            detail="请重新轮询同一 task_id，不要重新提交任务，否则会重复计费",
        )

    # ------------------------------------------------------------------
    async def fetch_transcription(self, result_url: str) -> dict[str, Any]:
        """拉取任务结果里给出的转写文件。

        这个 URL 是百炼临时生成的，不需要鉴权头。
        """
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(result_url)
        except httpx.HTTPError as exc:
            raise AsrError(f"拉取转写结果失败：{exc}") from exc

        if response.status_code >= 400:
            raise AsrError(
                f"拉取转写结果失败（HTTP {response.status_code}）",
                detail=response.text[:300],
            )
        return response.json()

    async def transcribe(
        self,
        file_url: str,
        *,
        duration_ms: int = 0,
        diarization: bool = True,
        speaker_count: int | None = 2,
        on_tick=None,
    ) -> AsrResult:
        """一次性完成「提交 → 轮询 → 拉取结果」。

        适合 CLI 和 smoke 脚本。正式流水线请用 submit + poll_until_done，
        以便在 submit 之后立刻把 task_id 落库。
        """
        task_id = await self.submit(
            file_url, diarization=diarization, speaker_count=speaker_count
        )
        task = await self.poll_until_done(task_id, duration_ms=duration_ms, on_tick=on_tick)

        results = (task.get("output") or {}).get("results") or []
        if not results:
            raise AsrError("转写任务成功但未返回结果", detail=str(task)[:500])

        first = results[0]
        subtask_status = first.get("subtask_status")
        if subtask_status and subtask_status != "SUCCEEDED":
            raise AsrError(
                f"转写子任务失败（{subtask_status}）",
                detail=str(first.get("message") or first)[:500],
            )

        transcription_url = first.get("transcription_url")
        if not transcription_url:
            raise AsrError("转写结果里没有 transcription_url", detail=str(first)[:500])

        payload = await self.fetch_transcription(transcription_url)
        return parse_transcription(payload, task_id=task_id)


def parse_transcription(payload: dict[str, Any], *, task_id: str = "") -> AsrResult:
    """把百炼返回的转写 JSON 解析成统一结构。

    说话人编号是**匿名**的（0/1/2…），不跨录音稳定，也不告诉你谁是面试官 ——
    角色判定是流水线里的独立一步，由大模型根据对话内容推断。
    """
    transcripts = (payload.get("transcripts") or [])
    if not transcripts:
        # 兼容另一种层级：{"transcription": {"transcripts": [...]}}
        transcripts = ((payload.get("transcription") or {}).get("transcripts") or [])

    segments: list[AsrSegment] = []
    full_text_parts: list[str] = []
    duration_ms = 0
    speaker_ids: set[int] = set()
    seq = 0

    for transcript in transcripts:
        duration_ms = max(
            duration_ms, int(transcript.get("content_duration_in_milliseconds") or 0)
        )
        if transcript.get("text"):
            full_text_parts.append(transcript["text"].strip())

        for sentence in transcript.get("sentences") or []:
            text = (sentence.get("text") or "").strip()
            if not text:
                continue

            speaker = sentence.get("speaker_id")
            # 没开说话人分离时可能没有这个字段，统一归为 0
            speaker_id = int(speaker) if speaker is not None else 0
            speaker_ids.add(speaker_id)

            segments.append(
                AsrSegment(
                    seq=seq,
                    speaker_raw_id=speaker_id,
                    start_ms=int(sentence.get("begin_time") or 0),
                    end_ms=int(sentence.get("end_time") or 0),
                    text=text,
                )
            )
            seq += 1

    full_text = "\n".join(full_text_parts) if full_text_parts else "\n".join(
        f"[说话人{seg.speaker_raw_id}] {seg.text}" for seg in segments
    )

    return AsrResult(
        task_id=task_id,
        text=full_text,
        segments=segments,
        duration_ms=duration_ms,
        speaker_count=len(speaker_ids),
        raw=payload,
    )


def render_transcript_with_speakers(result: AsrResult, *, with_timestamps: bool = True) -> str:
    """渲染成带说话人标记的纯文本，供大模型消费。

    合并相邻的同一说话人片段，避免输出变成一行一句的碎片 —— 那样既费 token
    又让模型难以理解对话的连贯性。
    """
    if not result.segments:
        return result.text

    lines: list[str] = []
    current_speaker: int | None = None
    buffer: list[str] = []
    buffer_start = 0

    def flush() -> None:
        if current_speaker is None or not buffer:
            return
        prefix = f"[说话人{current_speaker}]"
        if with_timestamps:
            prefix += f" [{_mmss(buffer_start)}]"
        lines.append(f"{prefix} {''.join(buffer)}")

    for seg in result.segments:
        if seg.speaker_raw_id != current_speaker:
            flush()
            current_speaker = seg.speaker_raw_id
            buffer = [seg.text]
            buffer_start = seg.start_ms
        else:
            buffer.append(seg.text)
    flush()

    return "\n".join(lines)


def _mmss(ms: int) -> str:
    total = int(ms / 1000)
    return f"{total // 60:02d}:{total % 60:02d}"
