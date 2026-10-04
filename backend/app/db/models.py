"""数据库表定义。

SQLModel 的好处：同一个类既是表结构，也是 API 的出入参模型，
不用像 Java 那样 Entity 和 DTO 各写一遍。

这些表存在的理由归结为三件事：
  1. 省钱 —— AudioAsset.sha256 让同一个录音重复上传时复用已有转写，
     Transcript.asr_task_id 让重跑只轮询不重新提交（百炼按音频时长计费）。
  2. 断点续跑 —— PipelineStage 记录每个阶段的产物与状态，进程崩了能接着跑。
  3. 记账 —— LlmCall 记录每次调用的 token 与估算费用，界面上能看到花了多少钱。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Interview(SQLModel, table=True):
    """一次面试。"""

    __tablename__ = "interview"

    id: str = Field(default_factory=_uuid, primary_key=True)
    title: str
    company: str | None = None
    position: str | None = None
    # created / processing / done / failed / canceled
    status: str = Field(default="created", index=True)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class AudioAsset(SQLModel, table=True):
    """上传的音频及其指纹。"""

    __tablename__ = "audio_asset"

    id: str = Field(default_factory=_uuid, primary_key=True)
    interview_id: str = Field(foreign_key="interview.id", index=True)
    original_filename: str
    # 预处理后的 16k/mono wav 路径。转写完成后会被删除以节省磁盘。
    local_path: str | None = None
    oss_key: str | None = None
    # 内容指纹：同一文件重复上传时用于复用转写结果，是最大的一处省钱点
    sha256: str = Field(index=True)
    size_bytes: int = 0
    duration_ms: int = 0
    sample_rate: int = 0
    channels: int = 0
    created_at: datetime = Field(default_factory=_now)


class PipelineRun(SQLModel, table=True):
    """一次流水线执行。"""

    __tablename__ = "pipeline_run"

    id: str = Field(default_factory=_uuid, primary_key=True)
    interview_id: str = Field(foreign_key="interview.id", index=True)
    # pending / running / done / failed / canceled
    # 默认是 pending 而不是 running：running 代表「正在执行」，进程重启时
    # 会被当成残留任务捡回来重跑。刚创建还没排队的 run 不该是那个状态。
    status: str = Field(default="pending", index=True)
    current_stage: str = "s0_preprocess"
    progress: int = 0
    message: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class PipelineStage(SQLModel, table=True):
    """单个阶段的执行记录 —— 幂等与断点续跑的核心。

    input_hash = sha256(上游产物 + prompt 版本 + 模型名)。
    相同 input_hash 且状态为 done，则直接复用 output_json；
    改了 prompt 会改变 input_hash，从而自动重跑相关阶段。
    """

    __tablename__ = "pipeline_stage"

    id: str = Field(default_factory=_uuid, primary_key=True)
    run_id: str = Field(foreign_key="pipeline_run.id", index=True)
    interview_id: str = Field(foreign_key="interview.id", index=True)
    stage: str = Field(index=True)
    # pending / running / done / failed / skipped
    status: str = Field(default="pending", index=True)
    attempt: int = 0
    input_hash: str = ""
    output_json: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Transcript(SQLModel, table=True):
    """一次转写的结果。"""

    __tablename__ = "transcript"

    id: str = Field(default_factory=_uuid, primary_key=True)
    interview_id: str = Field(foreign_key="interview.id", index=True)
    # 百炼的 task_id。重跑时凭它只轮询、不重新提交，避免重复计费。
    asr_task_id: str | None = None
    model: str = "paraformer-v2"
    language: str = "zh"
    duration_ms: int = 0
    speaker_count: int = 0
    # 带说话人标记的纯文本，供 LLM 直接消费
    text: str = ""
    # 经大模型纠错后的全文。下游阶段优先用它，为空则回退到 text。
    # ASR 对中英混合的技术名词识别很差（实测 SQL→circle、LangGraph→long graph），
    # 原文保留在 text 里便于对照。
    corrected_text: str | None = None
    # 原始响应，留作审计与问题排查
    raw_json: str = "{}"
    # 是否切片（仅当音频超过 2 小时才切片）
    is_sliced: bool = False
    slice_count: int = 1
    created_at: datetime = Field(default_factory=_now)


class TranscriptSegment(SQLModel, table=True):
    """句级转写结果。"""

    __tablename__ = "transcript_segment"

    id: str = Field(default_factory=_uuid, primary_key=True)
    transcript_id: str = Field(foreign_key="transcript.id", index=True)
    seq: int = 0
    # ASR 返回的匿名说话人编号，从 0 开始，不跨录音稳定
    speaker_raw_id: int = 0
    # 切片时编号只在块内有效，用 chunk_index 区分
    chunk_index: int = 0
    start_ms: int = 0
    end_ms: int = 0
    text: str = ""
    # ASR 原始文本在被大模型纠错后，错的那部分写在这里。
    # 为 None 表示这一句没有改动 —— 前端据此只高亮真正被改过的句子。
    corrected_text: str | None = None


class SpeakerMapping(SQLModel, table=True):
    """匿名 speaker 到语义角色（面试官 / 候选人）的映射。

    云 ASR 只给编号，不告诉你谁是面试官，这一步由 LLM 判定，
    判定结果落在这里，并允许人工改判（manual_override）。
    """

    __tablename__ = "speaker_mapping"

    id: str = Field(default_factory=_uuid, primary_key=True)
    interview_id: str = Field(foreign_key="interview.id", index=True)
    chunk_index: int = 0
    speaker_raw_id: int = 0
    # interviewer / candidate / unknown
    role: str = "unknown"
    confidence: float = 0.0
    evidence: str | None = None
    manual_override: bool = False
    created_at: datetime = Field(default_factory=_now)


class QaPair(SQLModel, table=True):
    """从转写里切出的「问题-回答」对。"""

    __tablename__ = "qa_pair"

    id: str = Field(default_factory=_uuid, primary_key=True)
    interview_id: str = Field(foreign_key="interview.id", index=True)
    seq: int = 0
    topic: str | None = None
    question_text: str = ""
    answer_text: str = ""
    asker_raw_id: int | None = None
    answerer_raw_id: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    # 追问：面试官在候选人回答后就同一主题继续发问
    is_followup: bool = False
    parent_qa_seq: int | None = None
    # 寒暄、设备调试等与考察无关的对话，不参与评分
    is_off_topic: bool = False


class QaAnalysis(SQLModel, table=True):
    """单道题的复盘分析。"""

    __tablename__ = "qa_analysis"

    id: str = Field(default_factory=_uuid, primary_key=True)
    qa_pair_id: str = Field(foreign_key="qa_pair.id", index=True)
    interview_id: str = Field(foreign_key="interview.id", index=True)
    overall_score: float = 0.0
    # {"技术准确性": 8, "表达结构": 7, ...}
    dimension_scores_json: str = "{}"
    strengths_json: str = "[]"
    weaknesses_json: str = "[]"
    # 具体到「更好的答法应该怎么说」
    improvement_json: str = "[]"
    knowledge_points_json: str = "[]"
    predicted_followups_json: str = "[]"
    model: str = ""
    prompt_version: str = ""
    raw_response: str = ""
    created_at: datetime = Field(default_factory=_now)


class Report(SQLModel, table=True):
    """整体复盘报告。"""

    __tablename__ = "report"

    id: str = Field(default_factory=_uuid, primary_key=True)
    interview_id: str = Field(foreign_key="interview.id", index=True)
    version: int = 1
    overall_score: float = 0.0
    summary_json: str = "{}"
    model: str = ""
    prompt_version: str = ""
    created_at: datetime = Field(default_factory=_now)


class LlmCall(SQLModel, table=True):
    """每次大模型调用的账本。"""

    __tablename__ = "llm_call"

    id: str = Field(default_factory=_uuid, primary_key=True)
    interview_id: str | None = Field(default=None, index=True)
    run_id: str | None = Field(default=None, index=True)
    stage: str = ""
    model: str = ""
    prompt_version: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_estimate: float = 0.0
    latency_ms: int = 0
    status: str = "ok"
    created_at: datetime = Field(default_factory=_now)
