"""Report clip boundaries from the alignment (spec 5.5 5-2, 5-3).

Ported from eval/poc/align_poc.py. Start:
1. lead onset = first character of the lead's first sentence; if it falls inside a silence, the
   script's first words were not read as written: move it to the silence end (LEAD_ONSET_IN_SILENCE);
2. the latest scene cut in [onset - 3 s, onset + 0.5 s] (the anchor shot) + 1 frame (the detected
   cut frame can be a one-frame blend);
3. unless the acoustic model hears >= 4 syllables between that cut and the silence before the
   lead: a greeting, possibly begun over the studio wide shot (11-05, 11-11), so it is left out;
4. without a usable cut: inside the silence before the lead (silence end - 0.5 s), else onset - 0.3 s.
End: the cut that opens the next aligned item (found from that item's lead onset the same way)
- ½ frame, if it lies within ±3 s of the sign-off end; otherwise sign-off end + 0.4 s (the
sign-off, last in its window, can absorb up to ~2 s of the following audio).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .aligner import AlignStatus, Flag, ItemAlignment, duration_off, signoff_index
from .logprobs import LogProbs

START_SNAP = (-3.0, 0.5)  # cut search range around the lead onset
END_CUT_RANGE = 3.0  # next item's cut must be within this of the sign-off end
END_PAD = 0.4
GREETING_SYLLABLES = 4  # syllables between cut and lead that mean "a greeting"
SILENCE_BEFORE_LEAD = (-1.5, 0.7)  # silence end within [onset - 1.5, onset + 0.7]
SILENCE_START_BACKOFF = 0.5
NO_SILENCE_BACKOFF = 0.3
ONSET_IN_SILENCE_TOLERANCE = 0.05


@dataclass
class ClipBoundary:
    order: int
    start: float
    end: float
    start_snap: str  # scene | silence | align
    end_snap: str  # next_item_cut | align
    lead_onset: float
    signoff_end: float
    flags: list[Flag] = field(default_factory=list)
    lead_score: float | None = None
    signoff_score: float | None = None


def refine_onset(t: float, silences: list[tuple[float, float]]) -> tuple[float, bool]:
    for a, b in silences:
        if a - ONSET_IN_SILENCE_TOLERANCE <= t < b - ONSET_IN_SILENCE_TOLERANCE:
            return b, True
    return t, False


def snap_before(t: float, cuts: np.ndarray) -> float | None:
    """Latest cut in [t - 3 s, t + 0.5 s]."""
    cand = cuts[(cuts >= t + START_SNAP[0]) & (cuts <= t + START_SNAP[1])]
    return float(cand.max()) if len(cand) else None


def clip_start(
    lead_onset: float, cuts: np.ndarray, silences: list[tuple[float, float]], lp: LogProbs, fps: float
) -> tuple[float, str]:
    cut = snap_before(lead_onset, cuts)
    gap = [
        (a, b)
        for a, b in silences
        if lead_onset + SILENCE_BEFORE_LEAD[0] <= b <= lead_onset + SILENCE_BEFORE_LEAD[1]
    ]
    if cut is not None and gap and lp.syllables_between(cut, gap[-1][0]) >= GREETING_SYLLABLES:
        cut = None  # the anchor said something else (a greeting) between the cut and the lead
    if cut is not None:
        return cut + 1.0 / fps, "scene"
    if gap:
        a, b = gap[-1]
        return max(a, b - SILENCE_START_BACKOFF), "silence"
    return lead_onset - NO_SILENCE_BACKOFF, "align"


def decide_boundaries(
    alignments: list[ItemAlignment],
    upload_orders: set[int],
    planned: dict[int, float | None],
    cuts: list[float],
    silences: list[tuple[float, float]],
    lp: LogProbs,
    fps: float,
) -> list[ClipBoundary]:
    """Boundaries for the aligned upload reports (others are only anchors)."""
    cut_arr = np.asarray(sorted(cuts), dtype=float)
    # Lead onset of every aligned item (after the silence correction): the next item's start.
    item_starts = [
        (a.order, refine_onset(a.onset, silences)[0])
        for a in alignments
        if a.status == AlignStatus.ALIGNED and a.sentences
    ]
    out: list[ClipBoundary] = []
    for a in alignments:
        if a.order not in upload_orders or a.status != AlignStatus.ALIGNED or not a.sentences:
            continue
        flags: list[Flag] = []
        lead_onset, moved = refine_onset(a.sentences[0].onset, silences)
        if moved:
            flags.append(Flag.LEAD_ONSET_IN_SILENCE)
        if Flag.LOW_CONFIDENCE in a.flags:
            flags.append(Flag.LOW_CONFIDENCE)
        k = signoff_index(a.sentences)
        if k is None:
            flags.append(Flag.NO_SIGNOFF)
            k = len(a.sentences) - 1
        signoff = a.sentences[k]
        start, start_snap = clip_start(lead_onset, cut_arr, silences, lp, fps)
        end, end_snap = signoff.offset + END_PAD, "align"
        nxt = next((t for o, t in item_starts if o > a.order), None)
        if nxt is not None:
            cut = snap_before(nxt, cut_arr)
            if cut is not None and abs(cut - signoff.offset) <= END_CUT_RANGE:
                end, end_snap = cut - 0.5 / fps, "next_item_cut"  # stop before the blended cut frame
        if duration_off(end - start, planned.get(a.order)):
            flags.append(Flag.DURATION_MISMATCH)
        out.append(
            ClipBoundary(
                order=a.order,
                start=round(start, 3),
                end=round(end, 3),
                start_snap=start_snap,
                end_snap=end_snap,
                lead_onset=round(lead_onset, 3),
                signoff_end=round(signoff.offset, 3),
                flags=flags,
                lead_score=a.sentences[0].score,
                signoff_score=signoff.score,
            )
        )
    return out
