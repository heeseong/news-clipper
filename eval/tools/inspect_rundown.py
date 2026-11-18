"""Parse an IF_MARSL_003-shaped items.json and write a human review report.

Usage:
    python eval/tools/inspect_rundown.py DATA_DIR [--out review.md]

DATA_DIR holds items.json and, optionally, pdf_extra.json (planned durations
and R/S format from the cue sheet print-out, used as ground truth here).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.newsroom.models import ItemType, RundownItem, SentenceKind  # noqa: E402
from app.newsroom.rundown import build_item, parse_duration  # noqa: E402


def load(data_dir: Path) -> list[tuple[RundownItem, dict]]:
    items = json.loads((data_dir / "items.json").read_text())["data"]
    extra_path = data_dir / "pdf_extra.json"
    extra = {e["order"]: e for e in json.loads(extra_path.read_text())} if extra_path.exists() else {}
    out = []
    for raw in items:
        order = int(str(raw["cueSheetItemOrderNumber"]))
        ex = extra.get(order, {})
        out.append((build_item(raw, parse_duration(ex.get("plannedDuration")), ex.get("department")), ex))
    return out


def fmt_s(sec: float | None) -> str:
    if sec is None:
        return "-"
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


def report(rows: list[tuple[RundownItem, dict]]) -> str:
    md: list[str] = []
    n_r = sum(1 for it, _ in rows if it.item_type == ItemType.REPORT)
    n_up = sum(1 for it, _ in rows if it.upload)
    md.append(f"# 원고 파싱 결과\n\n아이템 {len(rows)}개, 리포트 {n_r}개, 업로드 대상 {n_up}개\n")
    excluded = [it for it, _ in rows if it.item_type == ItemType.REPORT and not it.upload]
    for it in excluded:
        md.append(f"- 업로드 제외: {it.order}. {it.title.clean} — 출처 {it.source} ({it.source_reason})")
    if excluded:
        md.append("")

    md.append("## 아이템 형식 판별\n")
    md.append("| No | 큐시트 형식 | 판별 | 근거 | 예정 | 추정 | 제목 |")
    md.append("|---|---|---|---|---|---|---|")
    mismatches = 0
    for it, ex in rows:
        truth = ex.get("format") or "-"
        got = it.item_type.value
        ok = (truth == got) or (truth == "-" and got == "OTHER")
        mismatches += 0 if ok else 1
        mark = "" if ok else " ⚠️"
        md.append(
            f"| {it.order} | {truth} | {got}{mark} | {it.type_reason} | {fmt_s(it.planned_seconds)} "
            f"| {fmt_s(it.estimated_seconds)} | {it.title.clean[:40]} |"
        )
    md.append(f"\n형식 판별 불일치: {mismatches}건\n")

    md.append("## 리포트 상세\n")
    for it, _ in rows:
        if not it.upload or it.script is None:
            continue
        sc = it.script
        md.append(f"### {it.order}. {it.title.clean}\n")
        md.append(f"- 원제목: `{it.title.raw}`" + (f" / 시리즈: `{it.title.series}`" if it.title.series else ""))
        md.append(f"- 기자: {it.reporter} / 사인오프: {sc.signoff_reporter}")
        md.append(f"- 예정 {fmt_s(it.planned_seconds)} / 낭독 추정 {fmt_s(it.estimated_seconds)}")
        md.append(f"- 썸네일 문구: {sc.notes.thumbnail!r}")
        md.append(f"- 상단 자막: {sc.notes.top_caption!r}")
        md.append(f"- 크레딧: {', '.join(sc.notes.credits) or '-'}")
        md.append(f"- 경고: {', '.join(sc.warnings) or '없음'}")
        lead = sc.lead
        report_ = sc.report
        md.append(f"\n**클립 시작 기준 (리드 첫 문장)**: {lead[0].text if lead else '(없음)'}")
        last = next((s for s in reversed(sc.sentences) if s.kind == SentenceKind.SIGNOFF), None)
        md.append(f"**클립 끝 기준 (사인오프)**: {last.text if last else '(없음)'}\n")
        md.append("| 구간 | 종류 | 화자 | 문장 |")
        md.append("|---|---|---|---|")
        for s in lead + report_:
            md.append(f"| {s.section.value} | {s.kind.value} | {s.speaker or ''} | {s.text} |")
        md.append("")
    return "\n".join(md)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    text = report(load(args.data_dir))
    out = args.out or args.data_dir / "review.md"
    out.write_text(text)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
