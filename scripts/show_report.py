"""在命令行查看某场面试的复盘报告。

用法：
    python scripts/show_report.py                    # 最近一场
    python scripts/show_report.py --title 杭州        # 按标题筛选
    python scripts/show_report.py --list             # 列出所有面试及总分
    python scripts/show_report.py --cost             # 看各阶段花了多少钱

为什么要有它：前端要到最后才做，在那之前这是唯一能完整看报告的方式。
复盘的价值在于内容本身，不该被界面进度挡住。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from sqlmodel import Session, select  # noqa: E402

from app.db.engine import get_engine, init_db  # noqa: E402
from app.db.models import Interview, LlmCall, QaAnalysis, QaPair, Report  # noqa: E402

WIDTH = 78


def rule(char: str = "─", label: str = "") -> None:
    if label:
        pad = WIDTH - len(label) - 4
        print(f"\n{char * 2} {label} {char * max(pad, 0)}")
    else:
        print(char * WIDTH)


def list_interviews() -> int:
    with Session(get_engine()) as session:
        interviews = session.exec(select(Interview).order_by(Interview.created_at)).all()
        if not interviews:
            print("还没有任何面试记录。")
            return 1

        print(f"\n{'标题':<28}{'状态':<12}{'总分':<8}{'创建时间'}")
        rule()
        for interview in interviews:
            report = session.exec(
                select(Report)
                .where(Report.interview_id == interview.id)
                .order_by(Report.version.desc())
            ).first()
            score = f"{report.overall_score:.0f}" if report else "—"
            created = interview.created_at.strftime("%Y-%m-%d %H:%M")
            print(f"{interview.title[:26]:<28}{interview.status:<12}{score:<8}{created}")
        print()
    return 0


def show_cost() -> int:
    with Session(get_engine()) as session:
        calls = session.exec(select(LlmCall)).all()

    if not calls:
        print("还没有大模型调用记录。")
        return 1

    by_stage: dict[str, dict[str, float]] = {}
    for call in calls:
        entry = by_stage.setdefault(
            call.stage, {"n": 0, "in": 0, "out": 0, "cost": 0.0}
        )
        entry["n"] += 1
        entry["in"] += call.prompt_tokens
        entry["out"] += call.completion_tokens
        entry["cost"] += call.cost_estimate

    print(f"\n{'阶段':<26}{'调用':<6}{'输入 token':<12}{'输出 token':<12}{'成本':<10}")
    rule()
    total = 0.0
    for stage, e in sorted(by_stage.items(), key=lambda x: -x[1]["cost"]):
        total += e["cost"]
        print(
            f"{stage:<26}{int(e['n']):<6}{int(e['in']):<12}{int(e['out']):<12}${e['cost']:.4f}"
        )
    rule()
    print(f"{'合计':<26}{'':<6}{'':<12}{'':<12}${total:.4f}  ≈ ¥{total * 7.2:.2f}")
    print("\n注：不含语音转写费用（百炼按音频时长另计，每月 10 小时免费）。\n")
    return 0


def _print_report(session: Session, interview: Interview, report: Report) -> None:
    data = json.loads(report.summary_json)

    print()
    print("=" * WIDTH)
    print(f"  面试复盘报告 · {interview.title}")
    print(f"  总分 {data['overall_score']:.0f}/100   （报告第 {report.version} 版）")
    print("=" * WIDTH)

    print()
    print(data.get("assessment", ""))

    radar = data.get("competency_radar") or {}
    if radar:
        rule(label="能力雷达")
        for name, score in radar.items():
            filled = int(score / 5)
            bar = "█" * filled + "░" * (20 - filled)
            print(f"  {name:<26}{bar} {score:>3.0f}")

    for label, key, mark in (
        ("最值得保留的优势", "top_strengths", "+"),
        ("最该改的问题", "critical_weaknesses", "!"),
        ("跨题反复出现的模式", "recurring_patterns", "*"),
    ):
        items = data.get(key) or []
        if not items:
            continue
        rule(label=label)
        for item in items:
            print(f"  {mark} {item}")

    plan = data.get("improvement_plan") or []
    if plan:
        rule(label="复习计划")
        for group in plan:
            print(f"\n  【{group.get('area', '')}】")
            for action in group.get("actions") or []:
                print(f"    - {action}")

    predicted = data.get("predicted_result") or {}
    if predicted:
        rule(label=f"结果预判（把握：{predicted.get('confidence', '未知')}）")
        print(f"  {predicted.get('verdict', '')}")
        print(f"  {predicted.get('reason', '')}")

    # 逐题明细
    pairs = session.exec(
        select(QaPair).where(QaPair.interview_id == interview.id).order_by(QaPair.seq)
    ).all()
    analyses = {
        a.qa_pair_id: a
        for a in session.exec(
            select(QaAnalysis).where(QaAnalysis.interview_id == interview.id)
        ).all()
    }

    rule(label="逐题明细")
    print(f"\n  {'题号':<5}{'主题':<24}{'总分':<7}{'技术':<6}{'表达':<6}{'深度':<6}{'完整':<6}")
    for pair in pairs:
        analysis = analyses.get(pair.id)
        if analysis is None:
            continue
        dims = json.loads(analysis.dimension_scores_json or "{}")
        print(
            f"  {pair.seq:<5}{(pair.topic or '')[:22]:<24}{analysis.overall_score:<7}"
            f"{dims.get('技术准确性', '-'):<6}{dims.get('表达结构', '-'):<6}"
            f"{dims.get('深度', '-'):<6}{dims.get('完整性', '-'):<6}"
        )

    print("\n  用 --detail <题号> 查看某道题的完整分析。\n")


def _print_detail(session: Session, interview: Interview, seq: int) -> int:
    pair = session.exec(
        select(QaPair)
        .where(QaPair.interview_id == interview.id)
        .where(QaPair.seq == seq)
    ).first()
    if pair is None:
        print(f"找不到第 {seq} 题。")
        return 1

    analysis = session.exec(
        select(QaAnalysis).where(QaAnalysis.qa_pair_id == pair.id)
    ).first()
    if analysis is None:
        print(f"第 {seq} 题还没有评分结果。")
        return 1

    print()
    print("=" * WIDTH)
    print(f"  第 {seq} 题 · {pair.topic or '（无主题）'} · {analysis.overall_score}/10")
    print("=" * WIDTH)

    rule(label="面试官问")
    print(f"  {pair.question_text}")

    rule(label="候选人答")
    print(f"  {pair.answer_text}")

    dims = json.loads(analysis.dimension_scores_json or "{}")
    if dims:
        rule(label="维度得分")
        for name, score in dims.items():
            print(f"  {name:<12}{score}")

    if analysis.summary:
        rule(label="总评")
        print(f"  {analysis.summary}")

    for label, raw in (
        ("做对了什么", analysis.strengths_json),
        ("哪里不足", analysis.weaknesses_json),
        ("下次可以怎么答", analysis.improvement_json),
        ("需要补的知识点", analysis.knowledge_points_json),
        ("可能的追问", analysis.predicted_followups_json),
    ):
        items = json.loads(raw or "[]")
        if not items:
            continue
        rule(label=label)
        for item in items:
            if isinstance(item, dict):
                title = item.get("title", "")
                detail = item.get("detail", "")
                print(f"  - {title}")
                if detail:
                    print(f"      {detail}")
            else:
                print(f"  - {item}")

    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="查看面试复盘报告")
    parser.add_argument("--title", help="按标题片段筛选面试")
    parser.add_argument("--list", action="store_true", help="列出所有面试")
    parser.add_argument("--cost", action="store_true", help="查看大模型调用成本")
    parser.add_argument("--detail", type=int, metavar="题号", help="查看某道题的完整分析")
    args = parser.parse_args()

    init_db()

    if args.list:
        return list_interviews()
    if args.cost:
        return show_cost()

    with Session(get_engine()) as session:
        query = select(Interview).order_by(Interview.created_at.desc())
        if args.title:
            query = (
                select(Interview)
                .where(Interview.title.contains(args.title))
                .order_by(Interview.created_at.desc())
            )
        interview = session.exec(query).first()

        if interview is None:
            print("找不到面试记录。用 --list 看看有哪些。")
            return 1

        if args.detail is not None:
            return _print_detail(session, interview, args.detail)

        report = session.exec(
            select(Report)
            .where(Report.interview_id == interview.id)
            .order_by(Report.version.desc())
        ).first()

        if report is None:
            print(f"《{interview.title}》还没有生成报告。")
            return 1

        _print_report(session, interview, report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
