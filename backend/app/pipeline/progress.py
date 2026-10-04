"""进程内进度总线。

流水线跑在后台协程里，前端通过 SSE 订阅进度。这里做的是一个极简的
「按 run_id 分频道的发布/订阅」：

    publisher (流水线阶段)  --publish-->  频道
                                           ├── queue --> SSE 连接 A
                                           └── queue --> SSE 连接 B

**这是进程内状态，所以后端必须用 --workers 1 启动。**
多 worker 会导致「任务在 A 进程跑，你在 B 进程订阅进度」而永远收不到消息。
将来若要多进程，只需把 publish/subscribe 换成 Redis Pub/Sub，其他代码不用动。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 订阅队列的容量。慢客户端堆积到这个数就丢事件 —— 前端刷新时会重新拉一次
# 数据库快照，所以丢事件不会导致状态丢失，只会少几次中间进度刷新。
_QUEUE_SIZE = 64

_subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}


def subscribe(run_id: str) -> asyncio.Queue[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_SIZE)
    _subscribers.setdefault(run_id, set()).add(queue)
    logger.debug("SSE 订阅 %s（当前 %d 个）", run_id, len(_subscribers[run_id]))
    return queue


def unsubscribe(run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
    channel = _subscribers.get(run_id)
    if not channel:
        return
    channel.discard(queue)
    if not channel:
        _subscribers.pop(run_id, None)
    logger.debug("SSE 退订 %s", run_id)


def publish(run_id: str, event: dict[str, Any]) -> None:
    """向某个 run 的所有订阅者推送一条事件。没有订阅者时静默丢弃。"""
    for queue in list(_subscribers.get(run_id, ())):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # 慢客户端：丢掉最旧的一条再放新的，保证最新进度能到达
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                logger.debug("丢弃一条进度事件：%s", run_id)


def subscriber_count(run_id: str) -> int:
    return len(_subscribers.get(run_id, ()))
