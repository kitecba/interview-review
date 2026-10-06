"""面试相关的 REST 接口。

上传、列表、详情、转写、报告、成本。

**上传用流式写盘**：一段 90 分钟的面试录音可能上百 MB，`await file.read()`
会把它整个读进内存。这里按块写文件，内存占用与文件大小无关。

**上传后的顺序很重要**：先把 Interview / AudioAsset / PipelineRun 落库并 commit，
再入队执行。反过来的话，流水线可能在记录还存在事务里时就开始查它，查不到。
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.config import get_settings
from app.db.engine import get_engine
from app.db.models import (
    AudioAsset,
    Interview,
    LlmCall,
    PipelineRun,
    PipelineStage,
    QaAnalysis,
    QaPair,
    Report,
    SpeakerMapping,
    Transcript,
    TranscriptSegment,
)
from app.pipeline.runner import runner
from app.pipeline.state import STAGE_LABELS, STAGE_ORDER
from app.services.hashing import sha256_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["interviews"])

# 单个文件上限。一面 90 分钟的 mp3 大约 80–130 MB，给足余量。
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
_CHUNK = 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ----------------------------------------------------------------------
# 列表与上传
# ----------------------------------------------------------------------
@router.get("/interviews")
async def list_interviews() -> list[dict]:
    with Session(get_engine()) as session:
        interviews = session.exec(
            select(Interview).order_by(Interview.created_at.desc())
        ).all()

        out = []
        for interview in interviews:
            report = session.exec(
                select(Report)
                .where(Report.interview_id == interview.id)
                .order_by(Report.version.desc())
            ).first()
            out.append(
                {
                    "id": interview.id,
                    "title": interview.title,
                    "company": interview.company,
                    "position": interview.position,
                    "status": interview.status,
                    "created_at": _iso(interview.created_at),
                    # 没有报告时为 null，前端据此决定是否显示"查看报告"
                    "overall_score": report.overall_score if report else None,
                    "report_version": report.version if report else None,
                }
            )
        return out


@router.post("/interviews", status_code=201)
async def create_interview(
    file: UploadFile,
    title: str = Form(...),
    company: str | None = Form(None),
    position: str | None = Form(None),
) -> dict:
    if not (title or "").strip():
        raise HTTPException(status_code=422, detail="标题不能为空")
    if not file.filename:
        raise HTTPException(status_code=422, detail="缺少音频文件")

    settings = get_settings()
    settings.ensure_dirs()

    interview_id = uuid4().hex
    dest_dir = settings.uploads_path / interview_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 文件名来自客户端，只取 basename，避免路径穿越
    safe_name = Path(file.filename).name
    dest = dest_dir / safe_name

    written = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(_CHUNK):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB 上限",
                    )
                out.write(chunk)
    except HTTPException:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(dest_dir, ignore_errors=True)
        logger.exception("保存上传文件失败")
        raise HTTPException(status_code=500, detail=f"保存文件失败：{exc}") from exc

    if written == 0:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail="上传的文件是空的")

    content_hash = sha256_file(dest)

    with Session(get_engine()) as session:
        interview = Interview(
            id=interview_id,
            title=title.strip(),
            company=(company or "").strip() or None,
            position=(position or "").strip() or None,
            status="created",
        )
        session.add(interview)
        # 模型之间没有定义 relationship，SQLAlchemy 无从得知插入顺序，
        # 必须显式 flush 一次把父表写下去，否则子表会撞外键约束
        session.flush()

        session.add(
            AudioAsset(
                interview_id=interview_id,
                original_filename=safe_name,
                local_path=str(dest),
                sha256=content_hash,
                size_bytes=written,
            )
        )
        run = PipelineRun(interview_id=interview_id)
        session.add(run)
        session.commit()
        run_id = run.id

    await runner.enqueue(run_id)
    logger.info("已创建面试 %s（%s，%.1f MB）并开始处理", interview_id, safe_name, written / 1e6)

    return {"interview_id": interview_id, "run_id": run_id}


@router.delete("/interviews/{interview_id}", status_code=204)
async def delete_interview(interview_id: str) -> None:
    """删除面试及其全部关联数据。

    按外键依赖顺序删：先删子表再删父表。SQLite 的外键约束是打开的，
    顺序错了会直接报错。
    """
    settings = get_settings()

    with Session(get_engine()) as session:
        interview = session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail="面试记录不存在")

        for model in (QaAnalysis,):
            for row in session.exec(
                select(model).where(model.interview_id == interview_id)
            ).all():
                session.delete(row)
        session.commit()

        for model in (Report, QaPair, SpeakerMapping, Transcript):
            for row in session.exec(
                select(model).where(model.interview_id == interview_id)
            ).all():
                session.delete(row)
        session.commit()

        for stage in session.exec(
            select(PipelineStage).where(PipelineStage.interview_id == interview_id)
        ).all():
            session.delete(stage)
        for run in session.exec(
            select(PipelineRun).where(PipelineRun.interview_id == interview_id)
        ).all():
            session.delete(run)
        for asset in session.exec(
            select(AudioAsset).where(AudioAsset.interview_id == interview_id)
        ).all():
            session.delete(asset)
        session.commit()

        session.delete(interview)
        session.commit()

    # 本地文件与 OSS 对象都是尽力而为，删不掉不影响主流程
    shutil.rmtree(settings.uploads_path / interview_id, ignore_errors=True)
    for leftover in (
        settings.tmp_audio_path / f"{interview_id}.wav",
    ):
        leftover.unlink(missing_ok=True)

    logger.info("已删除面试 %s", interview_id)


# ----------------------------------------------------------------------
# 详情
# ----------------------------------------------------------------------
@router.get("/interviews/{interview_id}")
async def get_interview(interview_id: str) -> dict:
    with Session(get_engine()) as session:
        interview = session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail="面试记录不存在")

        asset = session.exec(
            select(AudioAsset).where(AudioAsset.interview_id == interview_id)
        ).first()
        run = session.exec(
            select(PipelineRun)
            .where(PipelineRun.interview_id == interview_id)
            .order_by(PipelineRun.created_at.desc())
        ).first()

        stage_rows = {}
        if run is not None:
            for row in session.exec(
                select(PipelineStage).where(PipelineStage.run_id == run.id)
            ).all():
                stage_rows[row.stage] = row

    # 八个阶段全部列出（含尚未执行的），前端时间线才完整
    stages = []
    for name in STAGE_ORDER:
        row = stage_rows.get(name)
        duration_ms = None
        if row and row.started_at and row.finished_at:
            duration_ms = int((row.finished_at - row.started_at).total_seconds() * 1000)
        stages.append(
            {
                "stage": name,
                "label": STAGE_LABELS.get(name, name),
                "status": row.status if row else "pending",
                "attempt": row.attempt if row else 0,
                "error": row.error if row else None,
                "started_at": _iso(row.started_at) if row else None,
                "finished_at": _iso(row.finished_at) if row else None,
                "duration_ms": duration_ms,
            }
        )

    return {
        "interview": {
            "id": interview.id,
            "title": interview.title,
            "company": interview.company,
            "position": interview.position,
            "status": interview.status,
            "created_at": _iso(interview.created_at),
        },
        "audio": (
            {
                "original_filename": asset.original_filename,
                "duration_ms": asset.duration_ms,
                "size_bytes": asset.size_bytes,
            }
            if asset
            else None
        ),
        "run": (
            {
                "id": run.id,
                "status": run.status,
                "progress": run.progress,
                "message": run.message,
                "current_stage": run.current_stage,
            }
            if run
            else None
        ),
        "stages": stages,
    }


# ----------------------------------------------------------------------
# 转写
# ----------------------------------------------------------------------
@router.get("/interviews/{interview_id}/transcript")
async def get_transcript(interview_id: str) -> dict:
    with Session(get_engine()) as session:
        interview = session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail="面试记录不存在")

        transcript = session.exec(
            select(Transcript).where(Transcript.interview_id == interview_id)
        ).first()
        if transcript is None:
            raise HTTPException(status_code=404, detail="还没有转写结果")

        segments = session.exec(
            select(TranscriptSegment)
            .where(TranscriptSegment.transcript_id == transcript.id)
            .order_by(TranscriptSegment.seq)
        ).all()
        mappings = session.exec(
            select(SpeakerMapping).where(SpeakerMapping.interview_id == interview_id)
        ).all()

    return {
        "speakers": [
            {
                "speaker_raw_id": m.speaker_raw_id,
                "role": m.role,
                "confidence": m.confidence,
                "evidence": m.evidence,
                "manual_override": m.manual_override,
            }
            for m in sorted(mappings, key=lambda x: x.speaker_raw_id)
        ],
        "segments": [
            {
                "seq": seg.seq,
                "speaker_raw_id": seg.speaker_raw_id,
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "text": seg.text,
                # 为 null 表示这句没被纠错过，前端据此决定要不要高亮
                "corrected_text": seg.corrected_text,
            }
            for seg in segments
        ],
    }


# ----------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------
@router.get("/interviews/{interview_id}/report")
async def get_report(interview_id: str) -> dict:
    with Session(get_engine()) as session:
        interview = session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail="面试记录不存在")

        report = session.exec(
            select(Report)
            .where(Report.interview_id == interview_id)
            .order_by(Report.version.desc())
        ).first()

        pairs = session.exec(
            select(QaPair)
            .where(QaPair.interview_id == interview_id)
            .order_by(QaPair.seq)
        ).all()
        analyses = {
            a.qa_pair_id: a
            for a in session.exec(
                select(QaAnalysis).where(QaAnalysis.interview_id == interview_id)
            ).all()
        }

    report_payload = None
    if report is not None:
        try:
            summary = json.loads(report.summary_json)
        except ValueError:
            summary = {}
        report_payload = {
            "version": report.version,
            "overall_score": report.overall_score,
            "model": report.model,
            "summary": summary,
        }

    qa_payload = []
    for pair in pairs:
        analysis = analyses.get(pair.id)
        qa_payload.append(
            {
                "seq": pair.seq,
                "topic": pair.topic,
                "question_text": pair.question_text,
                "answer_text": pair.answer_text,
                "is_followup": pair.is_followup,
                "is_off_topic": pair.is_off_topic,
                "analysis": _analysis_payload(analysis) if analysis else None,
            }
        )

    return {"report": report_payload, "qa": qa_payload}


def _analysis_payload(analysis: QaAnalysis) -> dict:
    def load(raw: str, fallback):
        try:
            value = json.loads(raw or "")
        except ValueError:
            return fallback
        return value

    return {
        "overall_score": analysis.overall_score,
        "summary": analysis.summary,
        "dimension_scores": load(analysis.dimension_scores_json, {}),
        "strengths": load(analysis.strengths_json, []),
        "weaknesses": load(analysis.weaknesses_json, []),
        "improvement": load(analysis.improvement_json, []),
        "knowledge_points": load(analysis.knowledge_points_json, []),
        "predicted_followups": load(analysis.predicted_followups_json, []),
    }


# ----------------------------------------------------------------------
# 说话人改判
# ----------------------------------------------------------------------
class SpeakerMappingItem(BaseModel):
    speaker_raw_id: int
    #: interviewer / candidate / auto。"auto" 表示删除人工改判、恢复自动判定。
    role: str


class SpeakerMappingUpdate(BaseModel):
    mappings: list[SpeakerMappingItem]


@router.put("/interviews/{interview_id}/speaker-mapping")
async def update_speaker_mapping(interview_id: str, body: SpeakerMappingUpdate) -> dict:
    """人工改判说话人角色。

    **为什么改判后要重跑**：角色映射参与 s5 的幂等键 —— 改了角色，切分、评分、
    汇总的结果都会随之失效，所以要自动从对应阶段重跑，而不是留下新旧混杂的数据。

    - 指定 interviewer/candidate：落一条 manual_override 记录，从 s5 重跑
      （角色判定 s4 的结果会被保留，不会被自动结果覆盖）。
    - 指定 auto：删除人工改判记录，从 s4 重跑（重新让大模型判定）。

    流水线正在运行时拒绝改判 —— 阶段产物正在被读写，此时改会造成竞态。
    """
    if not body.mappings:
        raise HTTPException(status_code=422, detail="mappings 不能为空")

    allowed = {"interviewer", "candidate", "auto"}
    for item in body.mappings:
        if item.role not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"说话人 {item.speaker_raw_id} 的角色必须是 {'、'.join(sorted(allowed))} 之一",
            )

    with Session(get_engine()) as session:
        interview = session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail="面试记录不存在")

        run = session.exec(
            select(PipelineRun)
            .where(PipelineRun.interview_id == interview_id)
            .order_by(PipelineRun.created_at.desc())
        ).first()
        if run is None:
            raise HTTPException(status_code=409, detail="该面试还没有运行记录，无法改判")

        # **立刻捕获**。SQLModel 默认 expire_on_commit：commit 之后所有属性过期，
        # session 关闭后再访问 run.id 会对游离实例触发刷新，直接抛
        # DetachedInstanceError（这个 bug 是接口测试抓出来的）。
        run_id = run.id

        if runner.is_active(run_id):
            raise HTTPException(
                status_code=409,
                detail="流水线正在运行，请等它结束（或取消）后再改判",
            )

        existing = session.exec(
            select(SpeakerMapping).where(SpeakerMapping.interview_id == interview_id)
        ).all()
        by_key = {(row.chunk_index, row.speaker_raw_id): row for row in existing}

        restore_auto = False
        for item in body.mappings:
            if item.role == "auto":
                # 恢复自动：删掉人工记录。s4 重跑时会重建自动判定行。
                for row in existing:
                    if row.speaker_raw_id == item.speaker_raw_id and row.manual_override:
                        session.delete(row)
                restore_auto = True
                continue

            row = by_key.get((0, item.speaker_raw_id))
            if row is None:
                row = SpeakerMapping(
                    interview_id=interview_id,
                    chunk_index=0,
                    speaker_raw_id=item.speaker_raw_id,
                )
            row.role = item.role
            row.confidence = 1.0
            row.evidence = "人工指定"
            row.manual_override = True
            session.add(row)
        session.commit()

        mappings = session.exec(
            select(SpeakerMapping).where(SpeakerMapping.interview_id == interview_id)
        ).all()

        # 在最后一次 commit **之前**序列化 —— commit 会让所有已加载属性过期，
        # 之后在游离实例上访问就会抛 DetachedInstanceError
        mappings_payload = [
            {
                "speaker_raw_id": m.speaker_raw_id,
                "role": m.role,
                "confidence": m.confidence,
                "evidence": m.evidence,
                "manual_override": m.manual_override,
            }
            for m in sorted(mappings, key=lambda x: x.speaker_raw_id)
        ]

        # 立即置为 processing，前端轮询才能马上恢复；正式的 RUNNING
        # 由执行器在开始执行时写入
        interview.status = "processing"
        session.add(interview)
        session.commit()

    # 有人工记录被删除（恢复自动）时，要连角色判定一起重跑
    from_stage = "s4_role_mapping" if restore_auto else "s5_qa_segmentation"
    enqueued = await runner.enqueue(run_id, from_stage=from_stage)
    if not enqueued:
        # is_active 检查和 enqueue 之间有竞态；enqueue 返回 False 兜住它
        raise HTTPException(status_code=409, detail="流水线刚刚开始运行，请稍后再试")

    logger.info(
        "说话人已改判：%s interview=%s 从 %s 重跑",
        [(m.speaker_raw_id, m.role) for m in body.mappings],
        interview_id,
        from_stage,
    )
    return {
        "run_id": run_id,
        "from_stage": from_stage,
        "mappings": mappings_payload,
    }


# ----------------------------------------------------------------------
# 成本
# ----------------------------------------------------------------------
@router.get("/interviews/{interview_id}/cost")
async def get_cost(interview_id: str) -> dict:
    with Session(get_engine()) as session:
        interview = session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail="面试记录不存在")

        calls = session.exec(
            select(LlmCall).where(LlmCall.interview_id == interview_id)
        ).all()

    by_stage: dict[str, dict] = {}
    for call in calls:
        entry = by_stage.setdefault(
            call.stage,
            {
                "stage": call.stage,
                "label": STAGE_LABELS.get(call.stage, call.stage),
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost": 0.0,
            },
        )
        entry["calls"] += 1
        entry["prompt_tokens"] += call.prompt_tokens
        entry["completion_tokens"] += call.completion_tokens
        entry["cost"] += call.cost_estimate

    total = sum(e["cost"] for e in by_stage.values())

    return {
        "total_cost": round(total, 4),
        "total_cost_cny": round(total * 7.2, 3),
        "items": sorted(by_stage.values(), key=lambda x: -x["cost"]),
    }
