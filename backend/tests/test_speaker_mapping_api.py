"""说话人改判接口的测试。

**刻意不用真实的转写数据跑全链路**：改判成功会触发 s5 之后的真实重跑
（DeepSeek 调用，约 ¥0.5）。这里的 fake 面试没有转写记录，重跑会在
「读取转写」这一步立刻失败——恰好验证了重跑被触发，又一分钱不花。

运行：
    backend/.venv/Scripts/python.exe backend/tests/test_speaker_mapping_api.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import warnings

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.db.engine import get_engine, init_db  # noqa: E402
from app.db.models import (  # noqa: E402
    Interview,
    PipelineRun,
    SpeakerMapping,
)
from app.main import app  # noqa: E402
from app.pipeline.runner import runner  # noqa: E402

FAKE_ID = "test-override-" + uuid4().hex[:8]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def setup() -> None:
    init_db()
    with Session(get_engine()) as s:
        s.add(Interview(id=FAKE_ID, title="改判测试", status="done"))
        s.flush()
        s.add(PipelineRun(interview_id=FAKE_ID, status="done"))
        s.add(
            SpeakerMapping(
                interview_id=FAKE_ID, speaker_raw_id=0, role="interviewer", confidence=0.85
            )
        )
        s.add(
            SpeakerMapping(
                interview_id=FAKE_ID, speaker_raw_id=1, role="candidate", confidence=0.9
            )
        )
        s.commit()


def cleanup() -> None:
    with Session(get_engine()) as s:
        for m in s.exec(
            select(SpeakerMapping).where(SpeakerMapping.interview_id == FAKE_ID)
        ).all():
            s.delete(m)
        for r in s.exec(
            select(PipelineRun).where(PipelineRun.interview_id == FAKE_ID)
        ).all():
            s.delete(r)
        iv = s.get(Interview, FAKE_ID)
        if iv:
            s.delete(iv)
        s.commit()


def latest_run_id() -> str:
    with Session(get_engine()) as s:
        run = s.exec(
            select(PipelineRun)
            .where(PipelineRun.interview_id == FAKE_ID)
            .order_by(PipelineRun.created_at.desc())
        ).first()
        assert run is not None
        return run.id


def main() -> int:
    setup()
    failures = 0
    try:
        with TestClient(app) as c:
            print("[1] 正常改判 candidate -> interviewer")
            r = c.put(
                f"/api/interviews/{FAKE_ID}/speaker-mapping",
                json={"mappings": [{"speaker_raw_id": 1, "role": "interviewer"}]},
            )
            print("    状态码:", r.status_code)
            if r.status_code != 200:
                print("    响应:", r.text[:300])
                failures += 1
            else:
                d = r.json()
                print("    重跑起点:", d["from_stage"])
                roles = {
                    m["speaker_raw_id"]: (m["role"], m["manual_override"])
                    for m in d["mappings"]
                }
                print("    映射:", roles)
                if d["from_stage"] != "s5_qa_segmentation":
                    print("    [失败] 改判应从 s5 重跑")
                    failures += 1
                if roles.get(1) != ("interviewer", True):
                    print("    [失败] 改判未生效")
                    failures += 1
                if roles.get(0) != ("interviewer", False):
                    print("    [失败] 未改判的记录不该被标记 manual")
                    failures += 1
            time.sleep(2)  # 让 worker 跑到失败（无转写，不花钱）

            print("[2] 恢复自动 auto")
            r = c.put(
                f"/api/interviews/{FAKE_ID}/speaker-mapping",
                json={"mappings": [{"speaker_raw_id": 1, "role": "auto"}]},
            )
            print("    状态码:", r.status_code)
            if r.status_code != 200:
                print("    响应:", r.text[:300])
                failures += 1
            else:
                d = r.json()
                print("    重跑起点:", d["from_stage"])
                manual = {
                    m["speaker_raw_id"]: m["manual_override"] for m in d["mappings"]
                }
                print("    映射:", manual)
                if d["from_stage"] != "s4_role_mapping":
                    print("    [失败] auto 应从 s4 重跑")
                    failures += 1
                if manual.get(1):
                    print("    [失败] auto 之后人工标记应被清除")
                    failures += 1
            time.sleep(2)

            print("[3] 非法角色")
            r = c.put(
                f"/api/interviews/{FAKE_ID}/speaker-mapping",
                json={"mappings": [{"speaker_raw_id": 0, "role": "boss"}]},
            )
            print("    状态码:", r.status_code, "(期望 422)")
            if r.status_code != 422:
                failures += 1

            print("[4] 空 mappings")
            r = c.put(
                f"/api/interviews/{FAKE_ID}/speaker-mapping",
                json={"mappings": []},
            )
            print("    状态码:", r.status_code, "(期望 422)")
            if r.status_code != 422:
                failures += 1

            print("[5] 不存在的面试")
            r = c.put(
                "/api/interviews/no-such-id/speaker-mapping",
                json={"mappings": [{"speaker_raw_id": 0, "role": "interviewer"}]},
            )
            print("    状态码:", r.status_code, "(期望 404)")
            if r.status_code != 404:
                failures += 1

            print("[6] 运行中改判")
            run_id = latest_run_id()
            runner._active.add(run_id)  # 模拟正在运行
            try:
                r = c.put(
                    f"/api/interviews/{FAKE_ID}/speaker-mapping",
                    json={"mappings": [{"speaker_raw_id": 0, "role": "candidate"}]},
                )
                print("    状态码:", r.status_code, "(期望 409)")
                if r.status_code != 409:
                    failures += 1

                print("[7] 运行中重跑")
                r = c.post(f"/api/interviews/{FAKE_ID}/runs", json={})
                print("    状态码:", r.status_code, "(期望 409)")
                if r.status_code != 409:
                    failures += 1
            finally:
                runner._active.discard(run_id)

    finally:
        cleanup()
        print("测试数据已清理")

    print()
    if failures:
        print(f"[失败] {failures} 项未通过")
        return 1
    print("[通过] 全部断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
