"""流水线的阶段定义与状态机。

阶段顺序是固定的；每个阶段的状态与产物持久化在 pipeline_stage 表里，
这是「断点续跑」和「改 prompt 自动重跑」的实现基础。

刻意不在这个文件里写任何业务逻辑 —— 它只描述「有哪些阶段、什么状态合法」，
这样流水线执行器、API 层、前端都能共用同一份定义，不会各写一份而走样。
"""

from __future__ import annotations

from enum import StrEnum


class Stage(StrEnum):
    """流水线的七个阶段。"""

    PREPROCESS = "s0_preprocess"
    UPLOAD_OSS = "s1_upload_oss"
    TRANSCRIBE = "s2_transcribe"
    ROLE_MAPPING = "s3_role_mapping"
    QA_SEGMENTATION = "s4_qa_segmentation"
    QA_SCORING = "s5_qa_scoring"
    SUMMARY = "s6_summary"


# 执行顺序
STAGE_ORDER: tuple[str, ...] = tuple(stage.value for stage in Stage)

# 中文标签，给前端时间线用
STAGE_LABELS: dict[str, str] = {
    Stage.PREPROCESS: "音频预处理",
    Stage.UPLOAD_OSS: "上传音频",
    Stage.TRANSCRIBE: "语音转写",
    Stage.ROLE_MAPPING: "区分说话人角色",
    Stage.QA_SEGMENTATION: "切分问答对",
    Stage.QA_SCORING: "逐题评分",
    Stage.SUMMARY: "生成整体报告",
}


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"  # 幂等命中，复用已有产物


class RunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


class InterviewStatus(StrEnum):
    CREATED = "created"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


# 合法的阶段状态迁移。集中在这里校验，避免出现「done 又变回 running」这种脏数据。
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    StageStatus.PENDING: {StageStatus.RUNNING, StageStatus.SKIPPED},
    StageStatus.RUNNING: {StageStatus.DONE, StageStatus.FAILED},
    StageStatus.FAILED: {StageStatus.RUNNING},  # 允许重跑
    StageStatus.DONE: {StageStatus.RUNNING},  # 允许因为改 prompt 而重跑
    StageStatus.SKIPPED: {StageStatus.RUNNING},
}


def can_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    return target in _ALLOWED_TRANSITIONS.get(current, set())


def stage_index(stage: str) -> int:
    """返回阶段在流水线中的序号，未知阶段返回 -1。"""
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return -1


def next_stage(stage: str) -> str | None:
    index = stage_index(stage)
    if index == -1 or index + 1 >= len(STAGE_ORDER):
        return None
    return STAGE_ORDER[index + 1]


def stages_from(stage: str, *, inclusive: bool = True) -> list[str]:
    """从某个阶段开始到结尾的阶段列表，用于「从这一步重跑」。"""
    index = stage_index(stage)
    if index == -1:
        return []
    return list(STAGE_ORDER[index if inclusive else index + 1 :])
