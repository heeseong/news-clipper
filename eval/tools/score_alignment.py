"""Run the service alignment (backend/app/align) on one broadcast and score it (plan M3.6, M3.8).

Usage:
    python eval/tools/score_alignment.py DATA_DIR [--recording FILE] [--tol 0.05]

DATA_DIR (under eval/data/, not committed) holds:
    items.json          IF_MARSL_003 response (API or eval/tools/marsl_pdf_export.py output)
    pdf_extra.json      optional: planned lengths from the print-out (PDF exports only)
    ground_truth.json   optional: reference boundaries, see below
    recording.*         the broadcast recording (or pass --recording)
Intermediate files are cached in DATA_DIR/work/ (align.wav, scenes.json, silences.json,
logprobs.npz, the last one takes a few minutes on CPU). A logprobs file from the PoC
(eval/poc/compute_logits.py) can be dropped in as work/logprobs.npz.

ground_truth.json — decide it before looking at the result (blind):
    {
      "fps": 29.97,
      "reports": [
        {"order": 3, "clip_start": 239.0, "clip_end": 344.08, "note": "greeting: starts in the silence"},
        {"order": 5, "expect": "rejected", "note": "wrong cue sheet"}
      ]
    }
`clip_start` is the first frame the clip should show (the anchor-shot cut, or the silence before
the lead), `clip_end` the cut that opens the next item. The PoC's keys `shot_start` / `next_cut`
are accepted too. A boundary is correct within --tol seconds (default 0.05 s, 1.5 frames at
29.97 fps: the clip starts 1 frame after the cut and ends ½ frame before the next one).

Each report gets one verdict:
    ok        automatic range, both boundaries within tolerance
    rejected  no automatic range (LOW_CONFIDENCE / DURATION_MISMATCH / NOT_FOUND): a reviewer sets it
    WRONG     automatic range with a boundary outside tolerance (the case that must not happen)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.align import acoustic  # noqa: E402
from app.align.aligner import AlignItem  # noqa: E402
from app.align.logprobs import LogProbs  # noqa: E402
from app.align.service import plan_clips, write_json  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.media import analysis  # noqa: E402
from app.media.ffmpeg import probe  # noqa: E402
from app.newsroom.rundown import build_item, parse_duration  # noqa: E402

VIDEO_EXT = (".mp4", ".mov", ".mxf", ".mkv", ".ts")


def find_recording(d: Path) -> Path:
    found = sorted(p for p in d.iterdir() if p.suffix.lower() in VIDEO_EXT)
    if not found:
        raise SystemExit(f"no recording in {d} (use --recording)")
    return found[0]


def load_items(d: Path) -> list[AlignItem]:
    raw = json.loads((d / "items.json").read_text(encoding="utf-8"))["data"]
    extra_path = d / "pdf_extra.json"
    extra = {e["order"]: e for e in json.loads(extra_path.read_text())} if extra_path.exists() else {}
    out = []
    for r in raw:
        order = int(str(r["cueSheetItemOrderNumber"]))
        ex = extra.get(order, {})
        it = build_item(r, parse_duration(ex.get("plannedDuration")), ex.get("department"))
        out.append(
            AlignItem(
                order=it.order,
                item_type=it.item_type,
                upload=it.upload,
                planned_seconds=it.planned_seconds,
                estimated_seconds=it.estimated_seconds,
                sentences=list(it.script.sentences) if it.script else [],
            )
        )
    return out


def prepare(rec: Path, work: Path):
    """align.wav, scenes, silences and log-probs, computed the way the pipeline does."""
    s = get_settings()
    work.mkdir(exist_ok=True)
    p = probe(rec)
    wav, scenes, silences, lpz = (work / n for n in ("align.wav", "scenes.json", "silences.json", "logprobs.npz"))
    if not wav.exists():
        print("extracting alignment audio …", flush=True)
        analysis.extract_align_audio(rec, wav, p, track=None)
    if not scenes.exists():
        print("detecting scene cuts …", flush=True)
        analysis.detect_scenes(rec, scenes, p, threshold=s.scene_threshold)
    if not silences.exists():
        analysis.detect_silences(wav, silences, p.duration, noise_db=s.silence_noise_db, min_seconds=s.silence_min_seconds)
    if lpz.exists():
        lp = LogProbs.load(lpz)
    else:
        print(f"computing log-probabilities with {s.align_model} (CPU) …", flush=True)
        t = time.monotonic()
        lp = acoustic.compute_logprobs(wav, s.align_model, chunk_seconds=s.align_chunk_seconds, threads=s.align_threads)
        print(f"  {lp.duration:.0f} s of audio in {time.monotonic() - t:.0f} s", flush=True)
        lp.save(lpz)
    cuts = [c["time"] for c in json.loads(scenes.read_text())["cuts"]]
    sil = [(r["start"], r["end"]) for r in json.loads(silences.read_text())["silences"]]
    return p, lp, cuts, sil


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--recording", type=Path)
    ap.add_argument("--tol", type=float, default=0.05)
    args = ap.parse_args()
    d = args.data_dir
    rec = args.recording or find_recording(d)
    p, lp, cuts, silences = prepare(rec, d / "work")
    items = load_items(d)
    result = plan_clips(lp, items, cuts, silences, p.fps)
    write_json(d / "work" / "alignment.json", result)

    print(f"\n{d.name}: {len(items)} items, {sum(i.upload for i in items)} upload reports, fps {p.fps:.3f}")
    for a in result.items:
        if a.status != "ALIGNED":
            print(f"  [{a.order:2d}] {a.status}")
            continue
        retry = f"  retried from cut {a.retried_from_cut:.2f}" if a.retried_from_cut is not None else ""
        print(f"  [{a.order:2d}] {a.onset:8.2f} - {a.offset:8.2f}  median {a.median_score:6.2f} "
              f"low {a.low_share:4.0%}  {' '.join(a.flags)}{retry}")

    gt_path = d / "ground_truth.json"
    gt = {r["order"]: r for r in json.loads(gt_path.read_text())["reports"]} if gt_path.exists() else {}
    print("\nreport  clip_start (err)        clip_end (err)          snap               flags   verdict")
    counts = {"ok": 0, "rejected": 0, "WRONG": 0, "unscored": 0}
    for c in result.clips:
        g = gt.get(c.order)
        ref_s = (g or {}).get("clip_start", (g or {}).get("shot_start"))
        ref_e = (g or {}).get("clip_end", (g or {}).get("next_cut"))
        if c.start is None:
            verdict = "rejected"  # not wrong: nothing reaches upload without a reviewer's range
        elif g is None or ref_s is None or ref_e is None:
            verdict = "unscored"
        else:
            ok = abs(c.start - ref_s) <= args.tol and abs(c.end - ref_e) <= args.tol
            verdict = "ok" if ok else "WRONG"
        counts[verdict] += 1

        def fmt(v, ref):
            if v is None:
                return f"{'—':>9}         "
            err = f"({v - ref:+.3f})" if ref is not None else ""
            return f"{v:9.3f} {err:9}"

        print(f"  {c.order:3d}   {fmt(c.start, ref_s)}   {fmt(c.end, ref_e)}   "
              f"{(c.start_snap or '-'):7} → {(c.end_snap or '-'):13}  {','.join(c.flags) or '-'}   {verdict}")
    for w in result.warnings:
        print(f"  warning: {w}")
    print("\n" + "  ".join(f"{k} {v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
