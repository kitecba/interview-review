"""内置流水线阶段的注册表。

新增阶段时在这里注册即可，执行器按 pipeline.state.STAGE_ORDER 的顺序调度，
注册表本身的顺序无关紧要。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.pipeline.stages.base import Stage
from app.pipeline.stages.s0_preprocess import PreprocessStage

# 只在类型检查时导入：runner 会反过来导入本模块来注册阶段，
# 运行时导入会形成循环。
if TYPE_CHECKING:
    from app.pipeline.runner import PipelineRunner

logger = logging.getLogger(__name__)

# 阶段类的清单。后续阶段（s1_upload_oss / s2_transcribe / s3_role_mapping …）
# 实现后加到这里。
_BUILTIN_STAGES: tuple[type[Stage], ...] = (
    PreprocessStage,
)


def register_builtin_stages(runner: PipelineRunner) -> None:
    for stage_cls in _BUILTIN_STAGES:
        stage = stage_cls()
        runner.register(stage)
        logger.debug("已注册阶段：%s（%s）", stage.name, stage.label)
    logger.info("已注册 %d 个流水线阶段", len(_BUILTIN_STAGES))
