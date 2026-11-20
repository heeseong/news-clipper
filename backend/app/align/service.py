"""The align step (pipeline 5): cue sheet scripts + signals → sentence times and report clips.

`plan_clips` is the pure part (no DB, no model): tests drive it with synthetic log-probs.
`store_result` writes alignments, sentences and DRAFT clips for a job.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import AlignedSentence, Clip, ClipStatus, Job, Rundown, RundownItem
from app.newsroom.models import ItemType, ParsedScript

from .aligner import AlignItem, AlignStatus, Flag, ItemAlignment, align_rundown
from .boundaries import ClipBoundary, decide_boundaries
from .logprobs import LogProbs

log = logging.getLogger(__name__)

# Flags that reject the automatic range: the reviewer sets it by hand (spec 5-3-1).
REJECTING = (Flag.LOW_CONFIDENCE, Flag.DURATION_MISMATCH, Flag.NOT_FOUND)
OVERLAP = "OVERLAP"
NO_SCRIPT = "NO_SCRIPT"
MISMATCH_SHARE = 0.5  # flagged share of aligned items that means "wrong cue sheet"
JOB_WARNING_PREFIX = "정렬: "


@dataclass
class ClipPlan:
    order: int
    status: ClipStatus
    start: float | None  # None = manual range
    end: float | None
    start_snap: str | None
    end_snap: str | None
    suggested_start: float | None
    suggested_end: float | None
    confidence: float | None
    flags: list[str] = field(default_factory=list)


@dataclass
class AlignmentResult:
    items: list[ItemAlignment]
    boundaries: list[ClipBoundary]
    clips: list[ClipPlan]
    warnings: list[str]


def align_items_from_db(items: list[RundownItem]) -> list[AlignItem]:
    out = []
    for it in items:
        script = ParsedScript.model_validate(it.parsed_script) if it.parsed_script else None
        out.append(
            AlignItem(
                order=it.item_no,
                item_type=ItemType(it.type),
                upload=it.upload,
                planned_seconds=it.planned_duration,
                estimated_seconds=it.estimated_duration,
                sentences=list(script.sentences) if script else [],
            )
        )
    return out


def plan_clips(
    lp: LogProbs,
    items: list[AlignItem],
    cuts: list[float],
    silences: list[tuple[float, float]],
    fps: float,
    pronunciations: dict[str, str] | None = None,
) -> AlignmentResult:
    alignments = align_rundown(lp, items, cuts, pronunciations=pronunciations)
    by_order = {a.order: a for a in alignments}
    uploads = [it for it in items if it.upload]
    planned = {it.order: it.planned_seconds for it in items}
    boundaries = decide_boundaries(alignments, {it.order for it in uploads}, planned, cuts, silences, lp, fps)
    by_boundary = {b.order: b for b in boundaries}

    clips: list[ClipPlan] = []
    for it in uploads:
        a = by_order[it.order]
        if a.status == AlignStatus.NOT_FOUND:
            clips.append(ClipPlan(it.order, ClipStatus.NOT_FOUND, None, None, None, None, None, None, None, [Flag.NOT_FOUND]))
            continue
        if a.status == AlignStatus.NO_SCRIPT:
            clips.append(ClipPlan(it.order, ClipStatus.DRAFT, None, None, None, None, None, None, None, [NO_SCRIPT]))
            continue
        b = by_boundary[it.order]
        flags = [str(f) for f in b.flags]
        rejected = any(f in REJECTING for f in b.flags)
        clips.append(
            ClipPlan(
                order=it.order,
                status=ClipStatus.DRAFT,
                start=None if rejected else b.start,
                end=None if rejected else b.end,
                start_snap=None if rejected else b.start_snap,
                end_snap=None if rejected else b.end_snap,
                suggested_start=b.start,
                suggested_end=b.end,
                confidence=a.median_score,
                flags=flags,
            )
        )
    # Neighbouring clips must not overlap (spec 5-4).
    ranged = sorted((c for c in clips if c.start is not None), key=lambda c: c.start)
    for prev, cur in zip(ranged, ranged[1:], strict=False):
        if cur.start < prev.end:
            for c in (prev, cur):
                if OVERLAP not in c.flags:
                    c.flags.append(OVERLAP)

    warnings = []
    aligned = [a for a in alignments if a.status == AlignStatus.ALIGNED]
    flagged = [a for a in aligned if Flag.LOW_CONFIDENCE in a.flags or Flag.DURATION_MISMATCH in a.flags]
    if aligned and len(flagged) >= len(aligned) * MISMATCH_SHARE:
        warnings.append(
            f"{JOB_WARNING_PREFIX}큐시트와 녹화본이 맞지 않을 수 있습니다 (정렬된 아이템 {len(aligned)}개 중 "
            f"{len(flagged)}개가 판정 실패). 큐시트를 다시 골라 주세요"
        )
    missing = [a.order for a in alignments if a.status == AlignStatus.NOT_FOUND]
    if missing:
        warnings.append(f"{JOB_WARNING_PREFIX}녹화본에서 찾지 못한 아이템: {', '.join(map(str, missing))}번")
    return AlignmentResult(alignments, boundaries, clips, warnings)


def write_json(path: Path, result: AlignmentResult) -> None:
    def item(a: ItemAlignment) -> dict[str, Any]:
        return {
            "order": a.order,
            "status": a.status,
            "window": a.window,
            "retried_from_cut": a.retried_from_cut,
            "median_score": a.median_score,
            "low_share": a.low_share,
            "flags": [str(f) for f in a.flags],
            "sentences": [
                {"seq": s.index, "kind": s.sentence.kind, "text": s.sentence.text, "start": round(s.onset, 3),
                 "end": round(s.offset, 3), "score": round(s.score, 3)}
                for s in a.sentences
            ],
        }

    data = {
        "items": [item(a) for a in result.items],
        "boundaries": [{**asdict(b), "flags": [str(f) for f in b.flags]} for b in result.boundaries],
        "clips": [{**asdict(c), "status": str(c.status)} for c in result.clips],
        "warnings": result.warnings,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def store_result(session: Session, job: Job, rundown: Rundown, result: AlignmentResult) -> None:
    items = {i.item_no: i for i in rundown.items}
    ids = [i.id for i in rundown.items]
    session.execute(delete(Clip).where(Clip.job_id == job.id))
    session.execute(delete(AlignedSentence).where(AlignedSentence.rundown_item_id.in_(ids)))
    for a in result.items:
        row = items[a.order]
        row.align_status = str(a.status)
        row.aligned_start = round(a.onset, 3) if a.onset is not None else None
        row.aligned_end = round(a.offset, 3) if a.offset is not None else None
        row.align_score = round(a.median_score, 3) if a.median_score is not None else None
        row.align_flags = [str(f) for f in a.flags]
        session.add_all(
            AlignedSentence(
                rundown_item_id=row.id,
                seq=s.index,
                section=str(s.sentence.section),
                kind=str(s.sentence.kind),
                text=s.sentence.text,
                start_sec=round(s.onset, 3),
                end_sec=round(s.offset, 3),
                score=round(s.score, 3),
            )
            for s in a.sentences
        )
    for c in result.clips:
        session.add(
            Clip(
                job_id=job.id,
                rundown_item_id=items[c.order].id,
                status=c.status,
                start_sec=c.start,
                end_sec=c.end,
                start_snap=c.start_snap,
                end_snap=c.end_snap,
                suggested_start=c.suggested_start,
                suggested_end=c.suggested_end,
                confidence=round(c.confidence, 3) if c.confidence is not None else None,
                flags=list(c.flags),
            )
        )
    job.warnings = [w for w in (job.warnings or []) if not w.startswith(JOB_WARNING_PREFIX)] + result.warnings
    session.flush()


def load_signals(scenes: Path, silences: Path) -> tuple[list[float], list[tuple[float, float]]]:
    cuts = [c["time"] for c in json.loads(scenes.read_text())["cuts"]] if scenes.exists() else []
    sil = (
        [(r["start"], r["end"]) for r in json.loads(silences.read_text())["silences"]] if silences.exists() else []
    )
    return cuts, sil


def job_rundown(session: Session, job_id: int) -> Rundown | None:
    return session.scalar(select(Rundown).where(Rundown.job_id == job_id))
