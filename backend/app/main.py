"""FastAPI 应用入口。

启动方式（**必须 --workers 1**）：

    cd backend
    .venv/Scripts/python -m uvicorn app.main:app --reload --workers 1

为什么不能开多 worker：流水线的任务队列和 SSE 进度推送都是进程内状态，
多 worker 会出现「任务在 A 进程执行、前端连到 B 进程订阅进度」而永远收不到消息。
单机自用场景下 1 个 worker 完全够用。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_health, routes_interviews, routes_runs
from app.core.config import get_settings
from app.core.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()

    settings = get_settings()
    settings.ensure_dirs()

    from app.db.engine import init_db

    init_db()
    logger.info("数据库就绪：%s", settings.db_path)

    # 启动流水线执行器。它会在启动时把上次崩溃留下的 running 状态 run 捡回来继续跑。
    from app.pipeline.runner import runner
    from app.pipeline.stages import register_builtin_stages

    register_builtin_stages(runner)
    await runner.start()

    missing = settings.missing_keys()
    if missing:
        # 不阻止启动 —— 否则连 /api/health 都打不开，用户没法自查缺了什么
        logger.warning("以下配置项尚未填写，相关功能不可用：%s", "、".join(missing))
    else:
        logger.info("配置齐全，模型：%s / %s", settings.deepseek_model, settings.bailian_asr_model)

    yield

    await runner.stop()
    logger.info("服务已停止")


app = FastAPI(
    title="面试复盘助手",
    description="上传面试录音，自动转写并生成结构化复盘报告",
    version="0.1.0",
    lifespan=lifespan,
)

# 开发期前端跑在 Vite 的 5173 端口。生产由 Vite 构建产物同源托管，届时可收紧。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_health.router)
app.include_router(routes_interviews.router)
app.include_router(routes_runs.router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "面试复盘助手",
        "docs": "/docs",
        "health": "/api/health",
    }
