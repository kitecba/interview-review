"""FastAPI 应用入口。

本地开发启动方式（**必须 --workers 1**）：

    cd backend
    .venv/Scripts/python -m uvicorn app.main:app --reload --workers 1

为什么不能开多 worker：流水线的任务队列和 SSE 进度推送都是进程内状态，
多 worker 会出现「任务在 A 进程执行、前端连到 B 进程订阅进度」而永远收不到消息。
单机自用场景下 1 个 worker 完全够用。

生产部署（Docker）：容器里同时托管前端构建产物，前后端同源，
无需 CORS；访问口令通过 APP_ACCESS_CODE 环境变量启用。
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

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

    if settings.app_access_code:
        logger.info("访问口令已启用（/api/health 除外）")
    else:
        logger.warning("未设置 APP_ACCESS_CODE，API 处于无鉴权状态 —— 仅限本机使用，不要暴露公网")

    yield

    await runner.stop()
    logger.info("服务已停止")


app = FastAPI(
    title="面试复盘助手",
    description="上传面试录音，自动转写并生成结构化复盘报告",
    version="0.1.0",
    lifespan=lifespan,
)

# 开发期前端跑在 Vite 的 5173 端口。生产是同源托管，此配置仅服务开发。
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


@app.middleware("http")
async def access_gate(request: Request, call_next):
    """共享口令鉴权（部署到公网时启用）。

    APP_ACCESS_CODE 留空则完全放行（本地开发）。启用后，除健康检查外的
    所有 /api 请求都要带 X-Access-Token 头或 ?token= 查询参数。
    """
    code = get_settings().app_access_code
    if code:
        path = request.url.path
        if path.startswith("/api") and path != "/api/health":
            supplied = request.headers.get("x-access-token") or request.query_params.get("token")
            if supplied != code:
                return JSONResponse(
                    {"detail": "需要访问口令"}, status_code=401
                )
    return await call_next(request)


app.include_router(routes_health.router)
app.include_router(routes_interviews.router)
app.include_router(routes_runs.router)

# ---------------------------------------------------------------------------
# 生产模式：托管前端构建产物。
#
# 本地开发不设 STATIC_DIR，走 Vite dev server；容器里 STATIC_DIR=/app/static。
# 静态托管启用时**不注册** "/" 的 JSON 路由（它会抢在 catch-all 之前匹配，
# 导致容器里打开首页拿到一坨 JSON 而不是前端页面），"/" 由下面的 catch-all
# 回退到 index.html。/api、/docs 等更具体的路由注册在前，不会被吞。
# ---------------------------------------------------------------------------
# 注意：不能用 Path("") 判断 —— Python 里 Path("") 就是 Path(".")（当前目录），
# 空值必须显式当「未设置」处理，否则本地开发会在 import 时因找不到 assets/ 崩掉。
_STATIC_DIR_RAW = os.environ.get("STATIC_DIR", "").strip()
_STATIC_DIR = Path(_STATIC_DIR_RAW) if _STATIC_DIR_RAW else None

if _STATIC_DIR and _STATIC_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    async def spa_fallback(full_path: str) -> FileResponse | JSONResponse:
        if full_path.startswith("api/"):
            # API 404 就该是 404，不能回个 HTML 让前端解析失败时更难排查
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = _STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")

    logger.info("前端静态托管已启用：%s", _STATIC_DIR)
elif _STATIC_DIR:
    # 设了但路径不对：不打断启动（API 仍可用），但要让人一眼看到原因
    logger.error("STATIC_DIR=%s 不存在，前端静态托管未启用，页面将无法访问", _STATIC_DIR)

if not _STATIC_DIR:

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "name": "面试复盘助手",
            "docs": "/docs",
            "health": "/api/health",
        }
