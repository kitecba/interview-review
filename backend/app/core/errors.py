"""领域异常。

关键设计：每个异常带一个 retryable 标志，流水线据此决定「重试 / 中止 / 交人工」。
ASR 那条尤其重要 —— 提交成功后的轮询超时只能重轮询，绝不能重新提交，
因为百炼是按音频时长计费的，重复提交就是重复花钱。
"""

from __future__ import annotations


class AppError(Exception):
    """所有业务异常的基类。"""

    retryable: bool = False
    code: str = "APP_ERROR"

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}" + (f" — {self.detail}" if self.detail else "")


class ConfigError(AppError):
    """配置缺失或非法。重试无用，必须改配置。"""

    code = "CONFIG_ERROR"


class AudioError(AppError):
    """ffmpeg 预处理失败。文件被占用、进程被杀这类情况重试有意义。"""

    code = "AUDIO_ERROR"
    retryable = True


class StorageError(AppError):
    """OSS 上传或签名失败。多为网络问题，可重试。"""

    code = "STORAGE_ERROR"
    retryable = True


class AsrError(AppError):
    """百炼转写任务失败。"""

    code = "ASR_ERROR"


class AsrTimeoutError(AsrError):
    """轮询超时。

    retryable=True，但调用方必须只重轮询 —— task_id 已落库，
    重新提交会产生第二次计费。
    """

    code = "ASR_TIMEOUT"
    retryable = True


class LlmError(AppError):
    """大模型调用失败（网络 / 限流 / 服务端错误）。"""

    code = "LLM_ERROR"
    retryable = True


class LlmJsonError(LlmError):
    """模型返回的内容不是合法 JSON，可触发一次修复调用。"""

    code = "LLM_INVALID_JSON"
