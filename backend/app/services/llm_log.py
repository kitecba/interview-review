"""大模型调用的账本。

每次调用都记一条，用于两件事：
  1. 界面上展示「这一场面试花了多少钱」—— 成本是这个项目里最需要可见的指标。
  2. 排查问题时能看出是哪一步、哪个 prompt 版本、多少次 token 导致了异常。

记账失败绝不影响主流程 —— 它是观测手段，不是业务逻辑。
"""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.db.engine import get_engine
from app.db.models import LlmCall

logger = logging.getLogger(__name__)


def record_call(
    *,
    stage: str,
    model: str,
    prompt_version: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_estimate: float,
    latency_ms: int,
    interview_id: str | None = None,
    run_id: str | None = None,
    status: str = "ok",
) -> None:
    try:
        with Session(get_engine()) as session:
            session.add(
                LlmCall(
                    interview_id=interview_id,
                    run_id=run_id,
                    stage=stage,
                    model=model,
                    prompt_version=prompt_version,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_estimate=cost_estimate,
                    latency_ms=latency_ms,
                    status=status,
                )
            )
            session.commit()
    except Exception as exc:  # pragma: no cover
        logger.warning("记录大模型调用失败（不影响主流程）：%s", exc)
