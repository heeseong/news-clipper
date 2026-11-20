"""Align the cue sheet's scripts to the recording, item by item (spec 5.5-2, 5-3-1).

Ported from eval/poc/align_poc.py (golden sets 11-02, 11-04, 11-05, 11-11: 34/34 boundaries).
The constants and the order of decisions are the PoC's; change them only with a re-run of the
golden sets (plan M3.8).

Walk the rundown in order with a cursor. For each item with a spoken script (reports and briefs):
- the window starts at the previous item's last sentence, which is aligned again as a guard so
  this item's first sentence has an explicit left neighbour;
- window length = (planned length + planned length of script-less items skipped since the last
  aligned item [+ preamble allowance for the first item]) × 1.5 + 60 s;
- if the result fails the plausibility checks (LOW_CONFIDENCE / DURATION_MISMATCH), the window
  start is moved to each later scene cut in turn and the first plausible result is kept: with a
  long stretch of unrelated audio at the start of the window (another newscast, headlines,
  bridge VCR) ctc-segmentation crams the whole text into it (11-04: 727 syllables in 31 s);
- if less audio than text is left, the item is NOT_FOUND (the recording ended).
Sentence times are the first and last character times: ctc-segmentation's utterance bounds are
padded by up to 0.5 s.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
from ctc_segmentation import (
    CtcSegmentationParameters,
    ctc_segmentation,
    determine_utterance_segments,
    prepare_text,
)

from app.newsroom.models import ItemType, Sentence, SentenceKind

from .korean_text import to_spoken_hangul
from .logprobs import LogProbs

WINDOW_SLACK = 1.5  # window = (planned + extra) * slack + margin
WINDOW_MARGIN = 60.0
PREAMBLE_ALLOWANCE = 300.0  # recordings can start with another newscast before the title
DEFAULT_ITEM_SECONDS = 60.0  # neither planned nor estimated length
GUARD_LEAD = 1.0  # the window starts this long before the guard sentence's onset
RETRY_MIN_GAP = 3.0  # cuts closer than this to the previous retried cut are skipped
RETRY_BEFORE_CUT = 0.5
MIN_WINDOW_FRAMES = 8000  # ctc-segmentation search band (frames)
LOW_SCORE = -5.0  # LOW_CONFIDENCE: median < -5 or more than 30 % of sentences below -5
LOW_SHARE = 0.30
DURATION_TOLERANCE = 0.20  # DURATION_MISMATCH: aligned length off by more than ±20 %


class Flag(StrEnum):
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    DURATION_MISMATCH = "DURATION_MISMATCH"
    NOT_FOUND = "NOT_FOUND"
    LEAD_ONSET_IN_SILENCE = "LEAD_ONSET_IN_SILENCE"
    NO_SIGNOFF = "NO_SIGNOFF"


class AlignStatus(StrEnum):
    ALIGNED = "ALIGNED"
    NOT_FOUND = "NOT_FOUND"  # the recording ends before the item
    NO_SCRIPT = "NO_SCRIPT"  # nothing to align (no spoken text)


@dataclass
class AlignItem:
    """What the aligner needs from a cue sheet item."""

    order: int
    item_type: ItemType
    upload: bool
    planned_seconds: float | None
    estimated_seconds: float | None
    sentences: list[Sentence]  # spoken sentences in script order (may be empty)


@dataclass
class TimedSentence:
    index: int  # position in AlignItem.sentences
    sentence: Sentence
    onset: float  # first character (seconds from file start)
    offset: float  # last character
    score: float  # ctc-segmentation confidence (log-probability per character; 0 is best)


@dataclass
class ItemAlignment:
    order: int
    status: AlignStatus
    sentences: list[TimedSentence] = field(default_factory=list)
    window: tuple[float, float] | None = None
    retried_from_cut: float | None = None
    median_score: float | None = None
    low_share: float | None = None
    flags: list[Flag] = field(default_factory=list)

    @property
    def onset(self) -> float | None:
        return self.sentences[0].onset if self.sentences else None

    @property
    def offset(self) -> float | None:
        return self.sentences[-1].offset if self.sentences else None


# --- checks (spec 5-3-1) -------------------------------------------------------------------------


def score_stats(scores: list[float]) -> tuple[float, float]:
    arr = np.asarray(scores, dtype=float)
    return float(np.median(arr)), float((arr < LOW_SCORE).mean())


def low_confidence(scores: list[float]) -> bool:
    median, share = score_stats(scores)
    return median < LOW_SCORE or share > LOW_SHARE


def duration_off(aligned: float, planned: float | None) -> bool:
    return bool(planned) and abs(aligned / planned - 1) > DURATION_TOLERANCE


def plausible(sentences: list[TimedSentence], planned: float | None) -> bool:
    return not low_confidence([s.score for s in sentences]) and not duration_off(
        sentences[-1].offset - sentences[0].onset, planned
    )


# --- text ----------------------------------------------------------------------------------------


def to_tokens(text: str, vocab: set[str], pronunciations: dict[str, str] | None = None) -> str:
    """Spoken Hangul with `|` for word gaps, keeping only tokens the model knows."""
    spoken = to_spoken_hangul(text, pronunciations)
    return "".join(ch for ch in spoken.replace(" ", "|") if ch in vocab)


# --- ctc-segmentation ----------------------------------------------------------------------------


class TooLittleAudio(Exception):
    """The window holds less audio than the text needs."""


def align_texts(lp: LogProbs, t0: float, t1: float, texts: list[str], full_search: bool) -> list[tuple[float, float, float]]:
    """(onset, offset, score) per text inside [t0, t1)."""
    rows, start = lp.window(t0, t1)
    cfg = CtcSegmentationParameters()
    cfg.char_list = list(lp.vocab)
    cfg.index_duration = lp.frame_sec
    cfg.blank = lp.blank
    cfg.space = "|"
    # ctc-segmentation searches a band around the evenly spread diagonal. With a long unrelated
    # stretch before the text (first item) the band must cover the whole window.
    cfg.min_window_size = max(MIN_WINDOW_FRAMES, rows.shape[0]) if full_search else MIN_WINDOW_FRAMES
    gt_mat, utt_begin = prepare_text(cfg, texts)
    try:
        timings, char_probs, _ = ctc_segmentation(cfg, rows, gt_mat)
    except AssertionError as e:  # "Audio is shorter than text!"
        raise TooLittleAudio(str(e)) from e
    segs = determine_utterance_segments(cfg, utt_begin, char_probs, timings, texts)
    out = []
    for i, (_a, _b, score) in enumerate(segs):
        onset = float(timings[utt_begin[i] + 1])
        offset = float(timings[utt_begin[i + 1] - 1])
        out.append((start + onset, start + offset, float(score)))
    return out


# --- the walk ------------------------------------------------------------------------------------


def align_rundown(
    lp: LogProbs,
    items: list[AlignItem],
    cuts: list[float],
    *,
    start: float | None = None,
    pronunciations: dict[str, str] | None = None,
) -> list[ItemAlignment]:
    """Align every item in rundown order. `cuts` are scene-cut times (seconds)."""
    vocab = set(lp.vocab)
    cut_arr = np.asarray(sorted(cuts), dtype=float)
    cursor = lp.offset if start is None else max(lp.offset, start)
    guard: tuple[str, float] | None = None  # previous item's last sentence (tokens, onset)
    skipped = 0.0  # planned seconds of script-less items since the last aligned item
    results: list[ItemAlignment] = []

    for it in items:
        spoken = [(i, s, to_tokens(s.text, vocab, pronunciations)) for i, s in enumerate(it.sentences)]
        spoken = [x for x in spoken if x[2]]
        if not spoken:
            # Title, headlines, bridge VCR: no script but air time. Widen the next window.
            skipped += it.planned_seconds or 0
            results.append(ItemAlignment(it.order, AlignStatus.NO_SCRIPT))
            continue
        texts = [x[2] for x in spoken]
        dur = it.planned_seconds or it.estimated_seconds or DEFAULT_ITEM_SECONDS
        w0 = max(lp.offset, guard[1] - GUARD_LEAD) if guard else cursor
        extra = skipped + (PREAMBLE_ALLOWANCE if guard is None else 0.0)
        w1 = w0 + (dur + extra) * WINDOW_SLACK + WINDOW_MARGIN
        skipped = 0.0
        first = guard is None

        def timed(raw: list[tuple[float, float, float]], spoken=spoken) -> list[TimedSentence]:
            return [TimedSentence(i, s, on, off, sc) for (i, s, _), (on, off, sc) in zip(spoken, raw, strict=True)]

        try:
            wt = ([guard[0]] if guard else []) + texts
            raw = align_texts(lp, w0, w1, wt, full_search=first)
            sents = timed(raw[1:] if guard else raw)
        except TooLittleAudio:
            results.append(ItemAlignment(it.order, AlignStatus.NOT_FOUND, window=(w0, w1), flags=[Flag.NOT_FOUND]))
            continue

        retried = None
        if not plausible(sents, it.planned_seconds):
            last = -1e9
            for c in cut_arr[(cut_arr > w0) & (cut_arr < w1 - dur)]:
                if c - last < RETRY_MIN_GAP:
                    continue
                last = float(c)
                try:
                    cand = timed(
                        align_texts(
                            lp, c - RETRY_BEFORE_CUT, c + dur * WINDOW_SLACK + WINDOW_MARGIN, texts, full_search=first
                        )
                    )
                except TooLittleAudio:
                    break
                if plausible(cand, it.planned_seconds):
                    sents, retried = cand, float(c)
                    break

        cursor = sents[-1].offset
        guard = (texts[-1], sents[-1].onset)
        median, share = score_stats([s.score for s in sents])
        flags = []
        if median < LOW_SCORE or share > LOW_SHARE:
            flags.append(Flag.LOW_CONFIDENCE)
        if duration_off(sents[-1].offset - sents[0].onset, it.planned_seconds):
            flags.append(Flag.DURATION_MISMATCH)
        results.append(
            ItemAlignment(
                it.order,
                AlignStatus.ALIGNED,
                sentences=sents,
                window=(w0, w1),
                retried_from_cut=retried,
                median_score=median,
                low_share=share,
                flags=flags,
            )
        )
    return results


def signoff_index(sentences: list[TimedSentence]) -> int | None:
    for k in range(len(sentences) - 1, -1, -1):
        if sentences[k].sentence.kind == SentenceKind.SIGNOFF:
            return k
    return None
