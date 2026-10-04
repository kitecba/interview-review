"""全局配置：从项目根目录的 .env 读取。

密钥一律走环境变量，绝不硬编码 —— 这个仓库要能安全地公开。
缺失关键项时由 missing_keys() 报出，供 /api/health 和 scripts/check_env.py 展示，
而不是在 import 阶段直接崩掉（否则连健康检查都跑不起来）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py 位于 <root>/backend/app/core/config.py，向上四层即项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "backend"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- DeepSeek：复盘报告生成 ----------
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"

    # ---------- 阿里云百炼：语音转写 ----------
    dashscope_api_key: str = ""
    bailian_asr_model: str = "paraformer-v2"
    bailian_base_url: str = "https://dashscope.aliyuncs.com"

    # ---------- 阿里云 OSS：音频托管 ----------
    oss_endpoint: str = ""
    oss_bucket: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_signed_url_ttl_seconds: int = 86400

    # ---------- 本地运行 ----------
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    data_dir: str = "backend/data"

    # ---------- 派生路径 ----------
    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def tmp_audio_path(self) -> Path:
        """预处理后的 wav 落在这里，转写完成后即删（D 盘空间紧张）。"""
        return self.data_path / "tmp_audio"

    @property
    def uploads_path(self) -> Path:
        """用户上传的原始音频。按 interview_id 分子目录保存。"""
        return self.data_path / "uploads"

    @property
    def db_path(self) -> Path:
        return self.data_path / "app.db"

    def ensure_dirs(self) -> None:
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.tmp_audio_path.mkdir(parents=True, exist_ok=True)
        self.uploads_path.mkdir(parents=True, exist_ok=True)

    def missing_keys(self) -> list[str]:
        """返回尚未配置的关键项名称。空列表代表配置齐全。"""
        required = {
            "DEEPSEEK_API_KEY": self.deepseek_api_key,
            "DASHSCOPE_API_KEY": self.dashscope_api_key,
            "OSS_ENDPOINT": self.oss_endpoint,
            "OSS_BUCKET": self.oss_bucket,
            "OSS_ACCESS_KEY_ID": self.oss_access_key_id,
            "OSS_ACCESS_KEY_SECRET": self.oss_access_key_secret,
        }
        return [name for name, value in required.items() if not value]


@lru_cache
def get_settings() -> Settings:
    return Settings()
