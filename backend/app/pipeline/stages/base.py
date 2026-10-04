"""阶段抽象。

每个流水线阶段都是一个「纯函数 + 幂等键」：

    input_hash = sha256(上游产物 + prompt 版本 + 模型名)

命中且状态为 done 时直接复用已有产物（标记为 skipped），不重新调用付费服务。
改了 prompt 会改变 hash，从而自动触发该阶段重跑 —— 这条解决的是
「改了提示词但结果没变」这类极难排查的问题。

阶段自己负责读写数据库，执行器只关心「跑哪个阶段、成功还是失败」。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StageContext:
    """阶段执行时拿到的上下文。"""

    interview_id: str
    run_id: str
    stage: str
    # 向进度总线汇报：(消息, 百分比或 None 表示不更新)
    emit: Callable[[str, int | None], None]
    # 协作式取消检查。阶段内耗时的循环应当定期调用它。
    is_canceled: Callable[[], bool] = lambda: False
    # 供阶段之间传递轻量中间结果，避免反复查库
    scratch: dict[str, Any] = field(default_factory=dict)


class StageError(Exception):
    """阶段内部抛出的错误，会被执行器转成阶段失败状态。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class Stage(ABC):
    """一个流水线阶段。"""

    #: 阶段标识，与 pipeline.state.Stage 的取值一致
    name: str = ""
    #: 中文名，用于日志和进度提示
    label: str = ""

    @abstractmethod
    async def compute_input_hash(self, ctx: StageContext) -> str:
        """计算本阶段的幂等键。

        必须把「上游产物 + prompt 版本 + 模型名」都算进去，
        否则改了 prompt 或上游数据后仍会错误地复用旧结果。
        """

    @abstractmethod
    async def execute(self, ctx: StageContext) -> dict[str, Any]:
        """执行阶段，返回可序列化的产物（会存进 pipeline_stage.output_json）。"""

    async def should_skip(self, ctx: StageContext, cached_output: dict[str, Any]) -> bool:
        """幂等命中时的额外校验。

        默认直接跳过。子类可以覆盖，比如检查产物依赖的外部资源是否还在
        （OSS 对象可能已被删除，此时即使 hash 相同也必须重跑）。
        """
        return True
