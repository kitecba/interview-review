"""提示词包。

导入这个包即触发所有提示词模块的注册 —— 需要新提示词时，
在这里 import 对应模块即可。
"""

from __future__ import annotations

from app.prompts import p_repair, p_role, p_segment  # noqa: F401
from app.prompts.registry import Prompt, all_prompts, get, register

__all__ = ["Prompt", "all_prompts", "get", "register"]
