"""环境自检：把「能不能跑起来」需要的条件一次性列清楚。

用法：
    python scripts/check_env.py

比 /api/health 多做的事：会检查 .env 文件是否真的存在、数据目录能不能写、
ffmpeg 能不能实际执行（而不只是找到路径）。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认按 GBK 输出，中文会变成乱码。强制切到 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.config import PROJECT_ROOT, get_settings  # noqa: E402

OK = "[OK]"
FAIL = "[!!]"
WARN = "[--]"


def _line(status: str, label: str, detail: str = "") -> None:
    print(f"  {status} {label}" + (f"  {detail}" if detail else ""))


async def main() -> int:
    print("\n=== 面试复盘助手 · 环境自检 ===\n")
    problems = 0

    # ---------- 1. .env ----------
    print("[1] 配置文件")
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        _line(OK, ".env 存在", str(env_file))
    else:
        _line(FAIL, ".env 不存在", "请复制 .env.example 为 .env 并填写")
        problems += 1

    settings = get_settings()
    missing = settings.missing_keys()
    if not missing:
        _line(OK, "六个密钥全部已配置")
    else:
        _line(FAIL, f"缺失 {len(missing)} 项配置", "、".join(missing))
        problems += 1

    # ---------- 2. 数据目录 ----------
    print("\n[2] 数据目录")
    try:
        settings.ensure_dirs()
        probe_file = settings.data_path / ".write_test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        _line(OK, "可写", str(settings.data_path))
    except Exception as exc:
        _line(FAIL, "不可写", str(exc))
        problems += 1

    # ---------- 3. ffmpeg ----------
    print("\n[3] ffmpeg（音频预处理必需）")
    from app.services import audio

    try:
        exe = audio.ffmpeg_exe()
        result = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, timeout=20
        )
        version_line = (result.stdout or result.stderr).splitlines()[0] if result.returncode == 0 else ""
        if result.returncode == 0:
            _line(OK, "可执行", version_line[:70])
            _line(OK, "路径", exe)
        else:
            _line(FAIL, "执行失败", f"退出码 {result.returncode}")
            problems += 1
    except Exception as exc:
        _line(FAIL, "不可用", str(exc))
        _line(WARN, "修复方式", "pip install imageio-ffmpeg  或在 .env 设置 FFMPEG_PATH")
        problems += 1

    # ---------- 4. 数据库 ----------
    print("\n[4] 数据库")
    try:
        from sqlmodel import text

        from app.db.engine import get_engine, init_db

        init_db()
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        _line(OK, "可用", str(settings.db_path))
        _line(OK, "表已建好", f"{len(__import__('sqlmodel').SQLModel.metadata.tables)} 张")
    except Exception as exc:
        _line(FAIL, "不可用", str(exc))
        problems += 1

    # ---------- 5. 联网（可选） ----------
    print("\n[5] 云服务连通性（可选，需要密钥）")
    if settings.deepseek_api_key:
        try:
            from app.services.llm_deepseek import DeepSeekClient

            models = await DeepSeekClient().list_models()
            _line(OK, "DeepSeek 可达", f"{len(models)} 个模型")
            if settings.deepseek_model in models:
                _line(OK, f"目标模型 {settings.deepseek_model} 在列表中")
            else:
                _line(WARN, f"目标模型 {settings.deepseek_model} 不在模型列表里", str(models[:8]))
        except Exception as exc:
            _line(FAIL, "DeepSeek 不可达", str(exc))
            problems += 1
    else:
        _line(WARN, "跳过 DeepSeek 检查（未配置 key）")

    if settings.dashscope_api_key:
        _line(OK, "百炼 key 已配置", "实际调用在 smoke_pipeline 里验证")
    else:
        _line(WARN, "跳过百炼检查（未配置 key）")

    if settings.oss_bucket and settings.oss_access_key_id:
        try:
            import oss2

            auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
            bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket)
            _line(OK, "OSS 可访问", f"{settings.oss_bucket} @ {settings.oss_endpoint}")
        except Exception as exc:
            _line(FAIL, "OSS 不可访问", str(exc))
            problems += 1
    else:
        _line(WARN, "跳过 OSS 检查（未配置 bucket / AK）")

    # ---------- 结论 ----------
    print("\n=== 结论 ===")
    if problems == 0:
        print(f"  {OK} 环境就绪，可以开始跑流水线了。\n")
        return 0

    print(f"  {FAIL} 有 {problems} 项需要处理。\n")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
