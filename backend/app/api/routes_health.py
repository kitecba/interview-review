"""健康检查路由。

设计意图：配置缺失是最常见也最难自查的问题（尤其是首次部署），
所以把「ffmpeg 装没装、六个密钥配没配、数据库能不能写」一次性摊开，
而不是等到跑流水线时才在某个深处报一个看不懂的错。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.config import get_settings
from app.db.engine import get_engine

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health(deep: bool = False) -> dict[str, Any]:
    """返回各组件的就绪状态。

    deep=true 时会真正联网探测 DeepSeek 与百炼（较慢），默认只做本地检查。
    """
    from app.services import audio

    settings = get_settings()

    checks: dict[str, Any] = {}

    # ---- ffmpeg ----
    try:
        exe = audio.ffmpeg_exe()
        checks["ffmpeg"] = {"ok": True, "path": exe}
    except Exception as exc:
        checks["ffmpeg"] = {"ok": False, "error": str(exc)}

    # ---- 配置 ----
    missing = settings.missing_keys()
    checks["config"] = {
        "ok": not missing,
        "missing": missing,
        "deepseek_model": settings.deepseek_model,
        "asr_model": settings.bailian_asr_model,
    }

    # ---- 数据库 ----
    try:
        from sqlalchemy import text

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = {"ok": True, "path": str(settings.db_path)}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)}

    # ---- 目录 ----
    checks["data_dir"] = {
        "ok": settings.data_path.exists(),
        "path": str(settings.data_path),
    }

    if deep:
        checks["deepseek_api"] = await _probe_deepseek(settings)
        checks["bailian_api"] = await _probe_bailian(settings)

    ready = all(
        value.get("ok")
        for key, value in checks.items()
        if key in {"ffmpeg", "config", "database"}
    )

    return {"ready": ready, "checks": checks}


async def _probe_deepseek(settings) -> dict[str, Any]:
    if not settings.deepseek_api_key:
        return {"ok": False, "error": "未配置 DEEPSEEK_API_KEY"}
    try:
        from app.services.llm_deepseek import DeepSeekClient

        models = await DeepSeekClient().list_models()
        return {
            "ok": True,
            "models": models,
            "configured_model_available": settings.deepseek_model in models,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _probe_bailian(settings) -> dict[str, Any]:
    if not settings.dashscope_api_key:
        return {"ok": False, "error": "未配置 DASHSCOPE_API_KEY"}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.bailian_base_url.rstrip('/')}/api/v1/tasks/not-a-real-task",
                headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            )
        # 鉴权失败会返回 401；能拿到 400/404 说明 key 本身是通的
        authorized = response.status_code != 401
        return {
            "ok": authorized,
            "status_code": response.status_code,
            "hint": None if authorized else "DASHSCOPE_API_KEY 无效或已过期",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
