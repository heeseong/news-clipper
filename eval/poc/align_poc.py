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
START_SNAP = (-3.0, 0.5)  # cut search range around lead start
END_SNAP = (-0.3, 2.5)  # cut search range around sign-off end
END_PAD = 0.4


def load_items(data_dir: Path):
    raw = json.loads((data_dir / "items.json").read_text())["data"]
    extra = {e["order"]: e for e in json.loads((data_dir / "pdf_extra.json").read_text())}
    items = []
    for r in raw:
        order = int(str(r["cueSheetItemOrderNumber"]))
        items.append(build_item(r, parse_duration(extra.get(order, {}).get("plannedDuration"))))
    return items


def to_tokens(text: str, vocab: set[str]) -> str:
    spoken = to_spoken_hangul(text).replace(" ", "|")
    return "".join(ch for ch in spoken if ch in vocab)


def align_window(lpz, frame_sec, vocab_list, blank, texts):
    """Return (start, end, score, onset, offset) per text.

    start/end are ctc-segmentation's padded utterance bounds (up to 0.5 s around
    the speech). onset/offset are the times of the first and last character.
    """
    cfg = CtcSegmentationParameters()
    cfg.char_list = list(vocab_list)
    cfg.index_duration = frame_sec
    cfg.blank = blank
    cfg.space = "|"
    cfg.min_window_size = 8000
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
    for it in load_items(d):
        if it.script is None:
            continue
        sents = [s for s in it.script.sentences if to_tokens(s.text, vocab)]
        texts = [to_tokens(s.text, vocab) for s in sents]
        dur = it.planned_seconds or it.estimated_seconds or 60
        # Start the window at the previous item's last sentence and align that sentence
        # too, so this item's first sentence has an explicit left neighbour.
        w0 = max(offset, guard[1] - 1.0) if guard else max(offset, cursor)
        w1 = w0 + dur * WINDOW_SLACK + WINDOW_MARGIN
        f0, f1 = int((w0 - offset) / frame_sec), min(len(lpz), int((w1 - offset) / frame_sec))
        window_texts = ([guard[0]] if guard else []) + texts
        try:
            segs = align_window(lpz[f0:f1], frame_sec, vocab_list, blank, window_texts)
        except AssertionError:  # less audio left than text: item not in the recording
            print(f"[{it.order:2d} {it.item_type.value}] NOT_FOUND (recording ends)  {it.title.clean[:30]}")
            continue
        if guard:
            segs = segs[1:]
        segs = [(w0 + a, w0 + b, sc, w0 + on, w0 + off) for a, b, sc, on, off in segs]
        cursor = segs[-1][4]
        guard = (texts[-1], segs[-1][3])
        onset, _ = refine_onset(segs[0][3], silences)
        item_starts.append((it.order, onset))
        print(f"[{it.order:2d} {it.item_type.value}] {segs[0][3]:7.2f} - {segs[-1][4]:7.2f}  "
              f"min score {min(s[2] for s in segs):6.2f}  {it.title.clean[:30]}")
        if it.item_type != ItemType.REPORT:
            continue
        signoff = next((i for i in range(len(sents) - 1, -1, -1) if sents[i].kind == SentenceKind.SIGNOFF), len(sents) - 1)
        lead_start, lead_score = segs[0][3], segs[0][2]  # onset of the first character
        lead_start, moved = refine_onset(lead_start, silences)
        flags = ["LEAD_ONSET_IN_SILENCE"] if moved else []
        end, end_score = segs[signoff][4], segs[signoff][2]  # offset of the last character
        clip_start, s_src = snap(lead_start, cuts, *START_SNAP, prefer="before")
        if s_src == "scene":
            clip_start += 1.0 / args.fps  # the detected cut frame is a one-frame blend: skip it
        else:
            # No cut right before the lead (e.g. the anchor greeted viewers first, or the
            # lead is read over another shot): start in the silence just before the lead.
            gap = [(a, b) for a, b in silences if lead_start - 1.5 <= b <= lead_start + 0.7]
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
