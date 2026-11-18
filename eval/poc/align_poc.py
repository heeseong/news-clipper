"""PoC: align cue-sheet scripts to a recording with CTC segmentation.

Usage:
    python eval/poc/align_poc.py DATA_DIR [--lpz lpz_kresnik.npz] [--start SEC]

DATA_DIR must contain items.json (IF_MARSL_003 shape), pdf_extra.json (planned
durations), the lpz file from compute_logits.py and, for scoring,
ground_truth.json plus scenes_ff.txt (scene cut times).

Approach: walk the rundown in order with a cursor. For each item that has a
spoken script (reports and briefs), align its sentences inside a window
starting at the cursor, then move the cursor to the end of the item. Report
boundaries are the lead's first sentence start and the sign-off end, then
snapped to the nearest scene cut (start: cut just before; end: cut just after).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from ctc_segmentation import CtcSegmentationParameters, ctc_segmentation, determine_utterance_segments, prepare_text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.align.korean_text import to_spoken_hangul  # noqa: E402
from app.newsroom.models import ItemType, SentenceKind  # noqa: E402
from app.newsroom.rundown import build_item, parse_duration  # noqa: E402

WINDOW_SLACK = 1.5  # window = planned duration * slack + margin
WINDOW_MARGIN = 60.0
# Recordings can start with unrelated material (another newscast) before the title.
PREAMBLE_ALLOWANCE = 300.0
START_SNAP = (-3.0, 0.5)  # cut search range around lead start
END_SNAP = (-0.3, 2.5)  # cut search range around sign-off end
END_PAD = 0.4
LOW_SCORE = -5.0  # spec 5-3-1: LOW_CONFIDENCE if median < -5 or > 30% of sentences below -5
LOW_SHARE = 0.30
DURATION_TOLERANCE = 0.20  # spec 5-4: DURATION_MISMATCH if aligned length is off by more than ±20%


def duration_off(aligned: float, planned: float | None) -> bool:
    return bool(planned) and abs(aligned / planned - 1) > DURATION_TOLERANCE


def confidence(scores: list[float]) -> tuple[float, float, bool]:
    arr = np.array(scores)
    median, share = float(np.median(arr)), float((arr < LOW_SCORE).mean())
    return median, share, (median < LOW_SCORE or share > LOW_SHARE)


def load_items(data_dir: Path):
    raw = json.loads((data_dir / "items.json").read_text())["data"]
    extra = {e["order"]: e for e in json.loads((data_dir / "pdf_extra.json").read_text())}
    items = []
    for r in raw:
        order = int(str(r["cueSheetItemOrderNumber"]))
        ex = extra.get(order, {})
        items.append(build_item(r, parse_duration(ex.get("plannedDuration")), ex.get("department")))
    return items


def to_tokens(text: str, vocab: set[str]) -> str:
    spoken = to_spoken_hangul(text).replace(" ", "|")
    return "".join(ch for ch in spoken if ch in vocab)


def align_window(lpz, frame_sec, vocab_list, blank, texts, full_search: bool = False):
    """Return (start, end, score, onset, offset) per text.

    start/end are ctc-segmentation's padded utterance bounds (up to 0.5 s around
    the speech). onset/offset are the times of the first and last character.
    """
    cfg = CtcSegmentationParameters()
    cfg.char_list = list(vocab_list)
    cfg.index_duration = frame_sec
    cfg.blank = blank
    cfg.space = "|"
    # ctc-segmentation searches a band around the "evenly spread" diagonal. With a long
    # unrelated stretch before the text (first item), widen it to the whole window.
    cfg.min_window_size = max(8000, lpz.shape[0]) if full_search else 8000
    gt_mat, utt_begin = prepare_text(cfg, texts)
    timings, char_probs, _ = ctc_segmentation(cfg, lpz, gt_mat)
    segs = determine_utterance_segments(cfg, utt_begin, char_probs, timings, texts)
    out = []
    for i, (a, b, sc) in enumerate(segs):
        onset = float(timings[utt_begin[i] + 1])
        offset = float(timings[utt_begin[i + 1] - 1])
        out.append((a, b, sc, onset, offset))
    return out


def refine_onset(t: float, silences: list[tuple[float, float]]) -> tuple[float, bool]:
    """If an aligned onset falls inside a silence, speech really starts at its end.

    Happens when the first words of the script were not read as written: the
    aligner parks those characters in the preceding silence.
    """
    for a, b in silences:
        if a - 0.05 <= t < b - 0.05:
            return b, True
    return t, False


SPEECH_MIN = 4  # recognised syllables that count as speech (music yields few or none)


def syllables_between(t0: float, t1: float, lpz, frame_sec: float, offset: float, vocab_list, blank: int) -> int:
    """Number of syllables the acoustic model hears in [t0, t1] (greedy CTC decode)."""
    if t1 <= t0:
        return 0
    f0, f1 = max(0, int((t0 - offset) / frame_sec)), min(len(lpz), int((t1 - offset) / frame_sec))
    ids = lpz[f0:f1].argmax(axis=1)
    n, prev = 0, None
    for k in ids:
        if k != blank and k != prev and vocab_list[k] != "|":
            n += 1
        prev = k
    return n


def snap(t: float, cuts: np.ndarray, lo: float, hi: float, prefer: str) -> tuple[float, str]:
    cand = cuts[(cuts >= t + lo) & (cuts <= t + hi)]
    if len(cand) == 0:
        return t, "align"
    return (float(cand.max()) if prefer == "before" else float(cand.min())), "scene"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--lpz", default="lpz_kresnik.npz")
    ap.add_argument("--start", type=float, default=None, help="cursor start (sec); default: lpz start")
    ap.add_argument("--cuts", default="scenes_ff10.txt", help="scene cut times file")
    ap.add_argument("--fps", type=float, default=30000 / 1001)
    ap.add_argument("--silences", default="silences.txt", help="silence end/duration file")
    args = ap.parse_args()
    d = args.data_dir

    z = np.load(d / args.lpz)
    lpz, frame_sec, offset = z["lpz"], float(z["frame_sec"]), float(z["offset_sec"])
    vocab_list = [str(v) for v in z["vocab"]]
    vocab, blank = set(vocab_list), int(z["pad_id"])
    cuts = np.loadtxt(d / args.cuts)
    sil = np.loadtxt(d / args.silences, ndmin=2)  # rows: silence_end, duration
    silences = [(e - dur, e) for e, dur in sil]
    gt = {r["order"]: r for r in json.loads((d / "ground_truth.json").read_text())["reports"]} if (
        d / "ground_truth.json"
    ).exists() else {}

    cursor = args.start if args.start is not None else offset
    results = []
    item_starts: list[tuple[int, float]] = []  # (order, first sentence onset) for every aligned item
    guard: tuple[str, float] | None = None  # previous item's last sentence (tokens, onset)
    items = load_items(d)
    planned_by_order = {it.order: it.planned_seconds for it in items}
    skipped = 0.0  # planned seconds of script-less items since the last aligned one
    for it in items:
        if it.script is None:
            # Items without a script (title, headlines, bridge VCR) still take air time:
            # widen the next window by their planned length.
            skipped += it.planned_seconds or 0
            continue
        sents = [s for s in it.script.sentences if to_tokens(s.text, vocab)]
        texts = [to_tokens(s.text, vocab) for s in sents]
        dur = it.planned_seconds or it.estimated_seconds or 60
        # Start the window at the previous item's last sentence and align that sentence
        # too, so this item's first sentence has an explicit left neighbour.
        w0 = max(offset, guard[1] - 1.0) if guard else max(offset, cursor)
        extra = skipped + (PREAMBLE_ALLOWANCE if guard is None else 0.0)
        w1 = w0 + (dur + extra) * WINDOW_SLACK + WINDOW_MARGIN
        skipped = 0.0
        def attempt(a0: float, a1: float, with_guard: bool):
            f0, f1 = int((a0 - offset) / frame_sec), min(len(lpz), int((a1 - offset) / frame_sec))
            wt = ([guard[0]] if (guard and with_guard) else []) + texts
            out = align_window(lpz[f0:f1], frame_sec, vocab_list, blank, wt, full_search=guard is None)
            if guard and with_guard:
                out = out[1:]
            return [(a0 + a, a0 + b, sc, a0 + on, a0 + off) for a, b, sc, on, off in out]

        def plausible(sg) -> bool:
            return not confidence([x[2] for x in sg])[2] and not duration_off(sg[-1][4] - sg[0][3], it.planned_seconds)

        try:
            segs = attempt(w0, w1, with_guard=True)
        except AssertionError:  # less audio left than text: item not in the recording
            print(f"[{it.order:2d} {it.item_type.value}] NOT_FOUND (recording ends)  {it.title.clean[:30]}")
            continue
        if not plausible(segs):
            # ctc-segmentation crams the text into whatever audio comes first when the window
            # starts well before the item (preamble, headlines, bridge VCR). Retry with the
            # window starting at each later scene cut; keep the first plausible alignment.
            last = -1e9
            for c in cuts[(cuts > w0) & (cuts < w1 - dur)]:
                if c - last < 3.0:
                    continue
                last = c
                try:
                    cand = attempt(c - 0.5, c + dur * WINDOW_SLACK + WINDOW_MARGIN, with_guard=False)
                except AssertionError:
                    break
                if plausible(cand):
                    print(f"[{it.order:2d}] retried from cut {c:.2f}")
                    segs = cand
                    break
        cursor = segs[-1][4]
        guard = (texts[-1], segs[-1][3])
        onset, _ = refine_onset(segs[0][3], silences)
        item_starts.append((it.order, onset))
        print(f"[{it.order:2d} {it.item_type.value}] {segs[0][3]:7.2f} - {segs[-1][4]:7.2f}  "
              f"median {confidence([x[2] for x in segs])[0]:6.2f} low {confidence([x[2] for x in segs])[1]:4.0%}"
              f"{'  LOW_CONFIDENCE' if confidence([x[2] for x in segs])[2] else ''}"
              f"{'  DURATION_MISMATCH' if duration_off(segs[-1][4] - segs[0][3], it.planned_seconds) else ''}"
              f"  {it.title.clean[:30]}")
        if not it.upload:  # briefs and other stations' reports: alignment anchors only
            continue
        signoff = next((i for i in range(len(sents) - 1, -1, -1) if sents[i].kind == SentenceKind.SIGNOFF), len(sents) - 1)
        lead_start, lead_score = segs[0][3], segs[0][2]  # onset of the first character
        lead_start, moved = refine_onset(lead_start, silences)
        flags = ["LEAD_ONSET_IN_SILENCE"] if moved else []
        if confidence([x[2] for x in segs])[2]:
            flags.append("LOW_CONFIDENCE")
        end, end_score = segs[signoff][4], segs[signoff][2]  # offset of the last character
        clip_start, s_src = snap(lead_start, cuts, *START_SNAP, prefer="before")
        # The silence right before the lead, if any.
        gap = [(a, b) for a, b in silences if lead_start - 1.5 <= b <= lead_start + 0.7]
        if s_src == "scene" and gap and (
            syllables_between(clip_start, gap[-1][0], lpz, frame_sec, offset, vocab_list, blank) >= SPEECH_MIN
        ):
            # The anchor said something else between the cut and the lead (a greeting,
            # possibly started over the studio wide shot): do not include it.
            s_src = "align"
        if s_src == "scene":
            clip_start += 1.0 / args.fps  # the detected cut frame is a one-frame blend: skip it
        else:
            # No usable cut right before the lead (greeting, or the lead is read over
            # another shot): start in the silence just before the lead.
            if gap:
                a, b = gap[-1]
                clip_start, s_src = max(a, b - 0.5), "silence"
            else:
                clip_start = lead_start - 0.3
        clip_end, e_src = end + END_PAD, "align"  # finalized below from the next item
        row = {
            "order": it.order, "title": it.title.clean, "flags": flags,
            "lead_start": round(lead_start, 2), "lead_score": round(lead_score, 2),
            "signoff_end": round(end, 2), "signoff_score": round(end_score, 2),
            "clip_start": round(clip_start, 3), "start_snapped_to": s_src,
            "clip_end": round(clip_end, 3), "end_snapped_to": e_src,
            "sentences": [
                {"kind": s.kind.value, "text": s.text, "start": round(on, 2), "end": round(off, 2), "score": round(sc, 2)}
                for s, (a, b, sc, on, off) in zip(sents, segs)
            ],
        }
        results.append(row)

    # Report end: the cut that opens the next item (its anchor shot), when it is
    # close to the sign-off; otherwise sign-off end + pad.
    for r in results:
        nxt = next((t for o, t in item_starts if o > r["order"]), None)
        if nxt is not None:
            cut, src = snap(nxt, cuts, *START_SNAP, prefer="before")
            if src == "scene" and r["signoff_end"] - 3.0 <= cut <= r["signoff_end"] + 3.0:
                # stop before the blended cut frame
                r["clip_end"], r["end_snapped_to"] = round(cut - 0.5 / args.fps, 3), "next_item_cut"
        planned = planned_by_order.get(r["order"])
        if duration_off(r["clip_end"] - r["clip_start"], planned):
            r["flags"].append("DURATION_MISMATCH")
        if r["order"] in gt:
            g = gt[r["order"]]
            def diff(a, b):
                return None if b is None else round(a - b, 2)

            r["error"] = {
                "lead_start_vs_speech": diff(r["lead_start"], g["speech_start"]),
                "signoff_end_vs_speech": diff(r["signoff_end"], g["speech_end"]),
                "clip_start_vs_cut": round(r["clip_start"] - g["shot_start"], 3),
                "clip_end_vs_next_cut": round(r["clip_end"] - g["next_cut"], 3),
            }

    print("\nreport  lead_start(err)   signoff_end(err)   clip_start(err vs cut)   clip_end(err vs cut)")
    for r in results:
        e = {k: ('-' if v is None else v) for k, v in r.get("error", {}).items()}
        print(f"  {r['order']:2d}   {r['lead_start']:7.2f} ({e.get('lead_start_vs_speech', '-'):>5})"
              f"   {r['signoff_end']:7.2f} ({e.get('signoff_end_vs_speech', '-'):>5})"
              f"   {r['clip_start']:8.3f} ({e.get('clip_start_vs_cut', '-'):>6}) {r['start_snapped_to']:5}"
              f"   {r['clip_end']:8.3f} ({e.get('clip_end_vs_next_cut', '-'):>6}) {r['end_snapped_to']}")
    for r in results:
        if r["flags"]:
            print(f"  flags[{r['order']}]: {', '.join(r['flags'])}")
    out = d / f"alignment_{Path(args.lpz).stem}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
