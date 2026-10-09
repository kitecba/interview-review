"""s2 转写阶段的「历史任务处理」分支测试。

背景：真实部署中遇到「历史任务已 FAILED，重跑却永远在轮询同一个死任务」的
bug。修复后的逻辑要先探测历史任务状态，再决定复用还是重新提交。

用闭包假客户端替换 BailianAsrClient 的方法，跑四条分支，零真实 API 调用：
  1. 历史任务 FAILED → 重新提交（且只提交一次），新 task_id 落库
  2. 历史任务 SUCCEEDED → 直接取结果，不重新提交
  3. 历史任务 PENDING → 继续轮询，不重新提交
  4. 历史任务查询 404（超过保留期被清理）→ 重新提交

运行：
    backend/.venv/Scripts/python.exe backend/tests/test_transcribe_reuse.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import warnings

warnings.filterwarnings("ignore")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlmodel import Session, select  # noqa: E402

from app.db.engine import get_engine, init_db  # noqa: E402
from app.db.models import (  # noqa: E402
    AudioAsset,
    Interview,
    PipelineRun,
    PipelineStage,
    Transcript,
    TranscriptSegment,
)
from app.pipeline.stages.base import StageContext  # noqa: E402
from app.pipeline.stages.s2_transcribe import TranscribeStage  # noqa: E402
from app.services import asr_bailian  # noqa: E402

FAKE_RESULT_TASK = {
    "output": {
        "task_status": "SUCCEEDED",
        "results": [
            {
                "subtask_status": "SUCCEEDED",
                "transcription_url": "http://fake/transcript.json",
            }
        ],
    }
}

FAKE_TRANSCRIPT_PAYLOAD = {
    "transcripts": [
        {
            "text": "你好，请自我介绍。",
            "content_duration_in_milliseconds": 2000,
            "sentences": [
                {"begin_time": 0, "end_time": 2000, "speaker_id": 0, "text": "你好，请自我介绍。"}
            ],
        }
    ]
}


def patch_fake_client(state: dict) -> None:
    """用闭包替换 BailianAsrClient 的方法。state 记录调用痕迹供断言。"""

    async def poll_once(self, task_id: str) -> dict:
        state["polls"].append(task_id)
        status = state["old_status"] if task_id == "old-task" else "SUCCEEDED"
        return {
            "output": {
                "task_id": task_id,
                "task_status": status,
                "results": FAKE_RESULT_TASK["output"]["results"],
            }
        }

    async def submit(self, file_url: str, **kwargs) -> str:
        state["submits"] += 1
        return "new-task"

    async def poll_until_done(self, task_id: str, **kwargs):
        state["polls"].append(task_id)
        if task_id == "new-task":
            return FAKE_RESULT_TASK
        if state["old_status"] == "FAILED" and task_id == "old-task":
            # 这正是要防的回归：FAILED 的历史任务应重新提交而不是继续轮询
            raise AssertionError("FAILED 历史任务被继续轮询 —— 重提交逻辑没生效")
        return FAKE_RESULT_TASK

    async def fetch_transcription(self, url: str) -> dict:
        return FAKE_TRANSCRIPT_PAYLOAD

    asr_bailian.BailianAsrClient.poll_once = poll_once
    asr_bailian.BailianAsrClient.submit = submit
    asr_bailian.BailianAsrClient.poll_until_done = poll_until_done
    asr_bailian.BailianAsrClient.fetch_transcription = fetch_transcription


def setup_case(case: str) -> str:
    init_db()
    iid = f"test-s2-{case}-{uuid4().hex[:8]}"
    with Session(get_engine()) as s:
        s.add(Interview(id=iid, title=case, status="processing"))
        s.flush()
        s.add(
            AudioAsset(
                interview_id=iid,
                original_filename="fake.wav",
                local_path=None,
                oss_key=f"interviews/{iid}/hash.wav",
                sha256=uuid4().hex,
                duration_ms=60_000,
            )
        )
        s.add(PipelineRun(interview_id=iid, status="running"))
        s.add(
            Transcript(
                interview_id=iid,
                asr_task_id="old-task",
                model="paraformer-v2",
                text="",
                raw_json="{}",
            )
        )
        s.commit()
    return iid


def cleanup(iid: str) -> None:
    with Session(get_engine()) as s:
        # TranscriptSegment 没有 interview_id，经由 transcript 关联删除
        for t in s.exec(select(Transcript).where(Transcript.interview_id == iid)).all():
            for seg in s.exec(
                select(TranscriptSegment).where(TranscriptSegment.transcript_id == t.id)
            ).all():
                s.delete(seg)
            # 模型间没有 relationship，unit of work 不知道依赖顺序，
            # 必须先 flush 把子表删掉再删主表
            s.flush()
            s.delete(t)
        for model in (PipelineStage, AudioAsset, PipelineRun):
            for row in s.exec(select(model).where(model.interview_id == iid)).all():
                s.delete(row)
        iv = s.get(Interview, iid)
        if iv:
            s.delete(iv)
        s.commit()


async def run_stage(iid: str) -> dict:
    stage = TranscribeStage()
    ctx = StageContext(
        interview_id=iid,
        run_id="test-run",
        stage=stage.name,
        emit=lambda msg, pct: None,
    )
    return await stage.execute(ctx)


async def case_failed() -> bool:
    """历史任务 FAILED → 重新提交一次，新 task_id 落库。"""
    iid = setup_case("failed")
    state = {"old_status": "FAILED", "submits": 0, "polls": []}
    patch_fake_client(state)
    try:
        out = await run_stage(iid)
        with Session(get_engine()) as s:
            t = s.exec(select(Transcript).where(Transcript.interview_id == iid)).first()
        ok = (
            state["submits"] == 1
            and t.asr_task_id == "new-task"
            and out["segment_count"] >= 1
        )
        print(
            f"[{'通过' if ok else '失败'}] FAILED 分支：submit {state['submits']} 次，"
            f"落库 task_id={t.asr_task_id}，句数 {out['segment_count']}"
        )
        return ok
    finally:
        cleanup(iid)


async def case_succeeded() -> bool:
    """历史任务 SUCCEEDED → 直接取结果，不重新提交。"""
    iid = setup_case("succeeded")
    state = {"old_status": "SUCCEEDED", "submits": 0, "polls": []}
    patch_fake_client(state)
    try:
        out = await run_stage(iid)
        with Session(get_engine()) as s:
            t = s.exec(select(Transcript).where(Transcript.interview_id == iid)).first()
        ok = state["submits"] == 0 and t.asr_task_id == "old-task" and out["segment_count"] >= 1
        print(
            f"[{'通过' if ok else '失败'}] SUCCEEDED 分支：submit {state['submits']} 次"
            "（期望 0），task_id 保持 old-task"
        )
        return ok
    finally:
        cleanup(iid)


async def case_pending() -> bool:
    """历史任务 PENDING → 继续轮询，不重新提交。"""
    iid = setup_case("pending")
    state = {"old_status": "PENDING", "submits": 0, "polls": []}
    patch_fake_client(state)
    try:
        await run_stage(iid)
        ok = state["submits"] == 0
        print(
            f"[{'通过' if ok else '失败'}] PENDING 分支：submit {state['submits']} 次"
            "（期望 0），继续轮询既有任务"
        )
        return ok
    finally:
        cleanup(iid)


async def case_404() -> bool:
    """历史任务查询 404（超保留期被清理）→ 重新提交。"""
    iid = setup_case("notfound")
    # 先铺通用假实现（提交、轮询、取结果），再把探测覆盖成 404
    state = {"old_status": "PENDING", "submits": 0, "polls": []}
    patch_fake_client(state)
    submit_state = {"submits": 0, "polls": []}

    async def poll_404(self, task_id: str) -> dict:
        from app.core.errors import AsrError

        raise AsrError("查询转写任务被拒绝（HTTP 404）")

    async def submit_ok(self, file_url: str, **kwargs) -> str:
        submit_state["submits"] += 1
        return "new-task"

    asr_bailian.BailianAsrClient.poll_once = poll_404
    asr_bailian.BailianAsrClient.submit = submit_ok

    try:
        out = await run_stage(iid)
        with Session(get_engine()) as s:
            t = s.exec(select(Transcript).where(Transcript.interview_id == iid)).first()
        ok = (
            submit_state["submits"] == 1
            and t.asr_task_id == "new-task"
            and out["segment_count"] >= 1
        )
        print(
            f"[{'通过' if ok else '失败'}] 404 分支：submit {submit_state['submits']} 次，"
            f"落库 task_id={t.asr_task_id}"
        )
        return ok
    finally:
        cleanup(iid)


async def main() -> int:
    results = [
        await case_failed(),
        await case_succeeded(),
        await case_pending(),
        await case_404(),
    ]
    print()
    if all(results):
        print("[通过] 四个分支全部符合预期")
        return 0
    print(f"[失败] {results.count(False)} 个分支不符合预期")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
