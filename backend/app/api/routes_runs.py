"""流水线运行的接口：查询进度、启动与重跑。

**重跑刻意复用同一个 run，而不是新建一个。**

原因是幂等缓存的作用域是 `(run_id, stage)`：每个 PipelineStage 记录都属于
某个 run。如果重跑时新建 run，所有阶段的缓存都会落空 —— 包括已经付过费的
语音转写。复用同一个 run 才能让「重跑」真正做到只重做需要重做的部分。

（转写本身还有第二层保护：s2 会先查库里有没有已存在的 task_id，有就续轮询
而不是重新提交。加上这一层，即使将来改成新建 run，也不会重复计费。）

阶段历史不会因为复用 run 而丢失 —— 报告有多版本（Report.version），
阶段记录本身也保留了每次执行的 attempt 次数。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db.engine import get_engine
from app.db.models import Interview, PipelineRun
from app.pipeline.runner import runner
from app.pipeline.state import STAGE_LABELS, STAGE_ORDER

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["runs"])


class RunRequest(BaseModel):
    #: 从哪个阶段开始重跑。不传则从当前阶段继续（已完成的部分会被复用）。
    from_stage: str | None = None


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    with Session(get_engine()) as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="运行记录不存在")

        return {
            "id": run.id,
            "interview_id": run.interview_id,
            "status": run.status,
            "progress": run.progress,
            "message": run.message,
            "current_stage": run.current_stage,
            "current_stage_label": STAGE_LABELS.get(run.current_stage, run.current_stage),
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        }


@router.post("/interviews/{interview_id}/runs")
async def start_run(interview_id: str, body: RunRequest | None = None) -> dict:
    """启动或重跑流水线。

    传入 `from_stage` 时会先把该阶段及其后续阶段的状态清掉，强制它们重做；
    更早的阶段仍然走缓存复用。
    """
    from_stage = body.from_stage if body else None

    if from_stage is not None and from_stage not in STAGE_ORDER:
        raise HTTPException(
            status_code=422,
            detail=f"未知阶段 {from_stage}。可选值：{'、'.join(STAGE_ORDER)}",
        )

    with Session(get_engine()) as session:
        interview = session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail="面试记录不存在")

        run = session.exec(
            select(PipelineRun)
            .where(PipelineRun.interview_id == interview_id)
            .order_by(PipelineRun.created_at.desc())
        ).first()

        if run is None:
            run = PipelineRun(interview_id=interview_id)
            session.add(run)
            session.commit()
            session.refresh(run)

        run_id = run.id

    if runner.is_active(run_id):
        raise HTTPException(
            status_code=409,
            detail="流水线正在运行，请等它结束（或取消）后再重跑",
        )

    # 立即置为 processing，前端轮询才能马上恢复；正式的 RUNNING 由执行器写入
    with Session(get_engine()) as session:
        db_interview = session.get(Interview, interview_id)
        if db_interview:
            db_interview.status = "processing"
            session.add(db_interview)
            session.commit()

    enqueued = await runner.enqueue(run_id, from_stage=from_stage)
    if not enqueued:
        # is_active 检查和 enqueue 之间有竞态，这里兜底
        raise HTTPException(status_code=409, detail="流水线刚刚开始运行，请稍后再试")

    logger.info("已启动流水线：interview=%s run=%s from_stage=%s", interview_id, run_id, from_stage)

    return {"run_id": run_id, "from_stage": from_stage}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    """协作式取消。

    注意：已经在云端执行的语音转写无法撤回，那部分费用已经产生。
    取消只影响本地后续阶段的处理。
    """
    with Session(get_engine()) as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="运行记录不存在")

    runner.cancel(run_id)
    logger.info("已请求取消：run=%s", run_id)
    return {"run_id": run_id, "status": "canceling"}
