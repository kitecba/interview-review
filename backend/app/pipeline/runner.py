"""流水线执行器。

**为什么不用 BackgroundTasks**：它绑定请求生命周期、没有持久化、应用重启即丢，
无法满足「断点续跑」。**为什么不用 Celery/RQ**：单机自用场景引入 broker 是过度设计。

采用「进程内 asyncio 队列 + SQLite 持久化」：启动时拉起常驻消费协程，
每个阶段的状态与产物写进 pipeline_stage 表。进程崩溃重启后扫描未完成的 run，
从最后一个 done 的阶段继续。

**因此后端必须 --workers 1** —— 队列和进度总线都是进程内状态。
将来若要多进程，把 progress.publish/subscribe 换成 Redis Pub/Sub 即可，其余不动。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.core.errors import AppError
from app.db.engine import get_engine
from app.db.models import Interview, PipelineRun, PipelineStage
from app.pipeline import progress
from app.pipeline.stages.base import Stage, StageContext, StageError
from app.pipeline.state import (
    STAGE_LABELS,
    STAGE_ORDER,
    InterviewStatus,
    RunStatus,
    StageStatus,
)

logger = logging.getLogger(__name__)

# 可重试错误的最大尝试次数（含首次）
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF = (2, 8)  # 第 1 次失败等 2 秒，第 2 次等 8 秒


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PipelineRunner:
    """单机串行的流水线执行器。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._canceled: set[str] = set()
        # 已在队列中或正在执行的 run。用于去重 —— 否则「启动时捡回未完成任务」
        # 和「显式入队」可能把同一个 run 排两遍。
        self._active: set[str] = set()
        self._stages: dict[str, Stage] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._worker = asyncio.create_task(self._consume(), name="pipeline-worker")
        logger.info("流水线执行器已启动")

        # 重启后把中断的 run 捡回来继续跑
        await asyncio.to_thread(self._requeue_unfinished)

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None
        logger.info("流水线执行器已停止")

    def register(self, stage: Stage) -> None:
        if not stage.name:
            raise ValueError("阶段必须有 name")
        self._stages[stage.name] = stage

    # ------------------------------------------------------------------
    # 入队与取消
    # ------------------------------------------------------------------
    async def enqueue(self, run_id: str, from_stage: str | None = None) -> None:
        if run_id in self._active:
            # 已经在排队或正在跑，重复入队会导致同一份工作执行两遍
            logger.debug("run 已在队列中，忽略重复入队：%s", run_id)
            return
        self._canceled.discard(run_id)
        self._active.add(run_id)
        await self._queue.put((run_id, from_stage))

    def cancel(self, run_id: str) -> None:
        """协作式取消。阶段内部需定期检查 is_canceled 才会及时响应。"""
        self._canceled.add(run_id)

    def is_canceled(self, run_id: str) -> bool:
        return run_id in self._canceled

    @property
    def queued(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------------
    # 消费循环
    # ------------------------------------------------------------------
    async def _consume(self) -> None:
        while True:
            run_id, from_stage = await self._queue.get()
            try:
                await self._execute_run(run_id, from_stage)
            except asyncio.CancelledError:
                raise
            except Exception:
                # 执行器自身出错不能让消费循环死掉，否则后续任务全堵住
                logger.exception("流水线执行异常：run=%s", run_id)
                self._mark_run_failed(run_id, "流水线执行器内部错误")
            finally:
                self._active.discard(run_id)
                self._queue.task_done()

    # ------------------------------------------------------------------
    # 执行一次 run
    # ------------------------------------------------------------------
    async def _execute_run(self, run_id: str, from_stage: str | None) -> None:
        with Session(get_engine()) as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                logger.warning("run 不存在：%s", run_id)
                return
            interview_id = run.interview_id
            start_at = from_stage or run.current_stage or STAGE_ORDER[0]
            run.status = RunStatus.RUNNING
            run.message = "开始执行"
            session.add(run)
            session.commit()

        if from_stage:
            # 从指定阶段重跑时，把它之后的阶段状态清掉（幂等键没变的话仍会命中复用）
            self._reset_stages_after(run_id, start_at)

        stages = self._stages_from(start_at)
        if not stages:
            logger.warning("没有可执行的阶段：start=%s", start_at)
            self._finish_run(run_id, interview_id, success=True)
            return

        total = len(stages)
        for index, stage in enumerate(stages):
            if self.is_canceled(run_id):
                logger.info("任务已取消：%s", run_id)
                self._mark_run_canceled(run_id, interview_id)
                return

            ctx = StageContext(
                interview_id=interview_id,
                run_id=run_id,
                stage=stage.name,
                emit=self._make_emitter(run_id, stage.name),
                is_canceled=lambda rid=run_id: self.is_canceled(rid),
            )

            base_progress = int(index / total * 100)
            span = int(100 / total)

            try:
                reused = await self._run_stage(stage, ctx, run_id, interview_id, base_progress, span)
            except StageError as exc:
                self._mark_run_failed(
                    run_id, exc.message, interview_id=interview_id, stage=stage.name,
                    retryable=exc.retryable,
                )
                return
            except AppError as exc:
                self._mark_run_failed(
                    run_id, str(exc), interview_id=interview_id, stage=stage.name,
                    retryable=exc.retryable,
                )
                return
            except Exception as exc:
                logger.exception("阶段异常：%s", stage.name)
                self._mark_run_failed(
                    run_id, f"{stage.name} 内部错误：{exc}", interview_id=interview_id,
                    stage=stage.name,
                )
                return

            if reused:
                logger.info("阶段 %s 命中缓存，跳过", stage.name)

        self._finish_run(run_id, interview_id, success=True)

    async def _run_stage(
        self,
        stage: Stage,
        ctx: StageContext,
        run_id: str,
        interview_id: str,
        base_progress: int,
        span: int,
    ) -> bool:
        """执行单个阶段。返回 True 表示复用了缓存产物。"""
        input_hash = await stage.compute_input_hash(ctx)

        with Session(get_engine()) as session:
            row = session.exec(
                select(PipelineStage)
                .where(PipelineStage.run_id == run_id)
                .where(PipelineStage.stage == stage.name)
            ).first()

            if row is None:
                row = PipelineStage(
                    run_id=run_id,
                    interview_id=interview_id,
                    stage=stage.name,
                )

            # 幂等命中：同一个输入、同一次产物，直接复用
            if (
                row.status == StageStatus.DONE
                and row.input_hash == input_hash
                and row.output_json
            ):
                cached = json.loads(row.output_json)
                if await stage.should_skip(ctx, cached):
                    row.status = StageStatus.SKIPPED
                    session.add(row)
                    session.commit()
                    ctx.emit(f"{STAGE_LABELS.get(stage.name, stage.name)} 复用已有结果", base_progress + span)
                    return True

            row.status = StageStatus.RUNNING
            row.input_hash = input_hash
            row.attempt += 1
            row.error = None
            row.started_at = _now()
            session.add(row)
            session.commit()
            attempt = row.attempt

        last_error: Exception | None = None
        for try_index in range(attempt, _MAX_ATTEMPTS + 1):
            try:
                ctx.emit(f"{STAGE_LABELS.get(stage.name, stage.name)}…", base_progress)
                output = await stage.execute(ctx)

                with Session(get_engine()) as session:
                    row = session.exec(
                        select(PipelineStage)
                        .where(PipelineStage.run_id == run_id)
                        .where(PipelineStage.stage == stage.name)
                    ).first()
                    if row:
                        row.status = StageStatus.DONE
                        row.output_json = json.dumps(output, ensure_ascii=False)
                        row.attempt = try_index
                        row.finished_at = _now()
                        session.add(row)

                    run = session.get(PipelineRun, run_id)
                    if run:
                        run.current_stage = stage.name
                        run.progress = base_progress + span
                        run.updated_at = _now()
                        session.add(run)
                    session.commit()

                ctx.emit(
                    f"{STAGE_LABELS.get(stage.name, stage.name)} 完成", base_progress + span
                )
                return False

            except (StageError, AppError) as exc:
                last_error = exc
                retryable = getattr(exc, "retryable", False)
                if not retryable or try_index >= _MAX_ATTEMPTS:
                    raise _as_stage_error(exc) from exc

                delay = _RETRY_BACKOFF[min(try_index - attempt, len(_RETRY_BACKOFF) - 1)]
                logger.warning(
                    "阶段 %s 失败（第 %d 次），%d 秒后重试：%s",
                    stage.name, try_index, delay, exc,
                )
                ctx.emit(
                    f"{STAGE_LABELS.get(stage.name, stage.name)} 失败，{delay} 秒后重试",
                    base_progress,
                )
                await asyncio.sleep(delay)

        raise _as_stage_error(last_error) if last_error else StageError("阶段执行失败")

    # ------------------------------------------------------------------
    # 状态落库与事件
    # ------------------------------------------------------------------
    def _make_emitter(self, run_id: str, stage: str):
        def emit(message: str, percent: int | None = None) -> None:
            with Session(get_engine()) as session:
                run = session.get(PipelineRun, run_id)
                if run:
                    run.current_stage = stage
                    run.message = message
                    if percent is not None:
                        run.progress = percent
                    run.updated_at = _now()
                    session.add(run)
                    session.commit()

            progress.publish(
                run_id,
                {
                    "run_id": run_id,
                    "stage": stage,
                    "stage_label": STAGE_LABELS.get(stage, stage),
                    "message": message,
                    "progress": percent,
                    "ts": _now().isoformat(),
                },
            )

        return emit

    def _finish_run(self, run_id: str, interview_id: str, *, success: bool) -> None:
        with Session(get_engine()) as session:
            run = session.get(PipelineRun, run_id)
            if run:
                run.status = RunStatus.DONE if success else RunStatus.FAILED
                run.progress = 100 if success else run.progress
                run.message = "全部完成" if success else run.message
                run.updated_at = _now()
                session.add(run)

            interview = session.get(Interview, interview_id)
            if interview:
                interview.status = InterviewStatus.DONE if success else InterviewStatus.FAILED
                interview.updated_at = _now()
                session.add(interview)
            session.commit()

        progress.publish(
            run_id,
            {
                "run_id": run_id,
                "stage": None,
                "status": "done" if success else "failed",
                "message": "全部完成" if success else "执行失败",
                "progress": 100 if success else None,
                "ts": _now().isoformat(),
            },
        )
        logger.info("run %s %s", run_id, "完成" if success else "失败")

    def _mark_run_failed(
        self,
        run_id: str,
        message: str,
        *,
        interview_id: str | None = None,
        stage: str | None = None,
        retryable: bool = False,
    ) -> None:
        with Session(get_engine()) as session:
            run = session.get(PipelineRun, run_id)
            if run:
                run.status = RunStatus.FAILED
                run.message = message
                run.updated_at = _now()
                session.add(run)
                interview_id = interview_id or run.interview_id

            if stage:
                row = session.exec(
                    select(PipelineStage)
                    .where(PipelineStage.run_id == run_id)
                    .where(PipelineStage.stage == stage)
                ).first()
                if row:
                    row.status = StageStatus.FAILED
                    row.error = message
                    row.finished_at = _now()
                    session.add(row)

            if interview_id:
                interview = session.get(Interview, interview_id)
                if interview:
                    interview.status = InterviewStatus.FAILED
                    interview.updated_at = _now()
                    session.add(interview)
            session.commit()

        progress.publish(
            run_id,
            {
                "run_id": run_id,
                "stage": stage,
                "status": "failed",
                "message": message,
                "retryable": retryable,
                "ts": _now().isoformat(),
            },
        )
        logger.error("run %s 失败于 %s：%s", run_id, stage, message)

    def _mark_run_canceled(self, run_id: str, interview_id: str) -> None:
        with Session(get_engine()) as session:
            run = session.get(PipelineRun, run_id)
            if run:
                run.status = RunStatus.CANCELED
                run.message = "已取消"
                run.updated_at = _now()
                session.add(run)
            interview = session.get(Interview, interview_id)
            if interview:
                interview.status = InterviewStatus.CANCELED
                session.add(interview)
            session.commit()

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _stages_from(self, start: str) -> list[Stage]:
        if start not in STAGE_ORDER:
            return []
        index = STAGE_ORDER.index(start)
        return [self._stages[name] for name in STAGE_ORDER[index:] if name in self._stages]

    def _reset_stages_after(self, run_id: str, start: str) -> None:
        """从某阶段重跑时，把它及其后续阶段的完成状态清掉。"""
        if start not in STAGE_ORDER:
            return
        tail = set(STAGE_ORDER[STAGE_ORDER.index(start) :])
        with Session(get_engine()) as session:
            rows = session.exec(
                select(PipelineStage).where(PipelineStage.run_id == run_id)
            ).all()
            for row in rows:
                if row.stage in tail and row.status in (StageStatus.DONE, StageStatus.SKIPPED):
                    row.status = StageStatus.PENDING
                    session.add(row)
            session.commit()

    def _requeue_unfinished(self) -> None:
        """启动时把未完成的 run 重新捡起来。

        只捡两种状态：
          - running —— 进程崩溃或重启留下的中断任务
          - pending —— 建好了但还没排上队的任务

        刻意不捡 done/failed/canceled。早期版本用「status == running」当残留判据，
        而 PipelineRun 的默认状态恰恰是 running，结果把刚建好、还没入队的 run
        也捡了一遍，同一个 run 被执行两次。默认值改成 pending 后这里才成立。

        直接 put_nowait 会绕过 enqueue 的去重，所以自己维护 _active。
        """
        with Session(get_engine()) as session:
            unfinished = session.exec(
                select(PipelineRun).where(
                    PipelineRun.status.in_([RunStatus.PENDING, RunStatus.RUNNING])
                )
            ).all()
            recovered = [(run.id, run.current_stage) for run in unfinished]

        for run_id, stage in recovered:
            if run_id in self._active:
                continue
            logger.info("捡回未完成的 run：%s（从 %s 继续）", run_id, stage or "起点")
            self._active.add(run_id)
            self._queue.put_nowait((run_id, stage))


def _as_stage_error(exc: Exception | None) -> StageError:
    if isinstance(exc, StageError):
        return exc
    if isinstance(exc, AppError):
        return StageError(str(exc), retryable=exc.retryable)
    return StageError(str(exc) if exc else "未知错误")


# 全局单例。在 app.main 的 lifespan 里 start()。
runner = PipelineRunner()
