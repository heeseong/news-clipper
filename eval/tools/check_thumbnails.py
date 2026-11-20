"""Check thumbnail candidates of straight briefs on a real broadcast (plan M4).

A brief's thumbnail should be a frame of the footage that shows the on-air title caption. Run
after eval/tools/score_alignment.py (it leaves work/alignment.json, work/scenes.json):

    # 1. Contact sheets of every brief, one frame per --step seconds, numbered (blind labelling)
    python eval/tools/check_thumbnails.py DATA_DIR sheets
    # 2. Write DATA_DIR/caption_labels.json by looking at the sheets only:
    #    {"step": 2.0, "briefs": {"7": "1111100", ...}}   1 = title caption visible, 0 = not
    # 3. Pick candidates with the service code and score them against the labels
    python eval/tools/check_thumbnails.py DATA_DIR run

Outputs go to DATA_DIR/thumbs/ (not committed).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.media.ffmpeg import probe  # noqa: E402
from app.newsroom.models import ItemType  # noqa: E402
from app.newsroom.rundown import build_item  # noqa: E402
from app.thumbnails.candidates import (  # noqa: E402
    anchor_end_for,
    candidate_times,
    extract_frame,
    find_candidates,
    measure,
)
from app.thumbnails.overlay import FONT  # noqa: E402

VIDEO_EXT = (".mp4", ".mov", ".mxf", ".mkv", ".ts")


def recording(d: Path) -> Path:
    return next(p for p in sorted(d.iterdir()) if p.stem == "recording" and p.suffix.lower() in VIDEO_EXT)


def briefs(d: Path) -> list[dict]:
    items = {}
    for raw in json.loads((d / "items.json").read_text())["data"]:
        item = build_item(raw)
        items[item.order] = item
    clips = json.loads((d / "work" / "alignment.json").read_text())["clips"]
    out = []
    for c in clips:
        item = items[c["order"]]
        if item.item_type != ItemType.BRIEF or c["start"] is None:
            continue  # reports, and briefs without an automatic range
        out.append({"order": c["order"], "start": c["start"], "end": c["end"], "title": item.title.clean})
    return out


def sheet(frames: list[tuple[str, Path]], out: Path, cols: int = 6) -> None:
    w, h = 320, 180
    rows = (len(frames) + cols - 1) // cols
    img = Image.new("RGB", (cols * w, rows * h), "black")
    font = ImageFont.truetype(str(FONT), 18)
    for i, (label, path) in enumerate(frames):
        with Image.open(path) as f:
            img.paste(f.resize((w, h)), ((i % cols) * w, (i // cols) * h))
        d = ImageDraw.Draw(img)
        x, y = (i % cols) * w, (i // cols) * h
        d.rectangle([x, y, x + 8 + 11 * len(label), y + 22], fill="black")
        d.text((x + 4, y + 1), label, font=font, fill="yellow")
    img.save(out, quality=85)


def cmd_sheets(d: Path, step: float) -> None:
    src = recording(d)
    p = probe(src)
    out = d / "thumbs" / "sheets"
    out.mkdir(parents=True, exist_ok=True)
    for b in briefs(d):
        frames = []
        t, i = b["start"] + step / 2, 0
        while t < b["end"]:
            f = extract_frame(src, p, t, out / f"{b['order']}_{i:02d}.jpg")
            frames.append((f"{b['order']}-{i}", f))
            t += step
            i += 1
        sheet(frames, out / f"brief_{b['order']:02d}.jpg")
        print(f"{b['order']:>3} {b['start']:8.2f}–{b['end']:8.2f} {len(frames):>3} frames  {b['title']}")


def cmd_run(d: Path) -> None:
    src = recording(d)
    p = probe(src)
    cuts = [c["time"] for c in json.loads((d / "work" / "scenes.json").read_text())["cuts"]]
    labels_file = d / "caption_labels.json"
    labels = json.loads(labels_file.read_text()) if labels_file.exists() else None
    out = d / "thumbs" / "run"
    out.mkdir(parents=True, exist_ok=True)
    hits = total = 0
    agree = n_lab = 0
    in_all = n_all = 0
    for b in briefs(d):
        anchor_end = anchor_end_for("brief", b["start"], b["end"], cuts, None)
        times = candidate_times(b["start"], b["end"], cuts, anchor_end)
        picked = find_candidates(src, p, times, out / f"work_{b['order']}", 6, kind="brief")
        sheet([(f"{i + 1}. {c.time:.1f}s cap {c.details['caption']:.2f}", c.path) for i, c in enumerate(picked)],
              out / f"picks_{b['order']:02d}.jpg", cols=3)
        line = f"{b['order']:>3} anchor→{anchor_end - b['start']:5.1f}s  top: " + ", ".join(
            f"{c.time - b['start']:.1f}s(cap {c.details['caption']:.2f}, {c.score:.2f})" for c in picked[:3])
        if labels and str(b["order"]) in labels["briefs"]:
            step = labels["step"]
            marks = labels["briefs"][str(b["order"])]

            def label(t: float, b=b, marks=marks, step=step) -> str | None:
                i = int((t - b["start"]) // step)
                return marks[i] if 0 <= i < len(marks) else None

            top = label(picked[0].time)
            in_all += sum(label(c.time) == "1" for c in picked)
            n_all += len(picked)
            total += 1
            hits += top == "1"
            line += f"  → top has caption: {'YES' if top == '1' else 'no'}"
            # Per-frame: does the caption score agree with the labels on the labelled frames?
            for i, m in enumerate(marks):
                f = d / "thumbs" / "sheets" / f"{b['order']}_{i:02d}.jpg"
                if f.exists():
                    n_lab += 1
                    agree += (measure(f, None)["caption"] >= 0.3) == (m == "1")
        print(line)
    if total:
        print(f"\ntop pick shows the caption: {hits}/{total}")
        print(f"candidates showing the caption: {in_all}/{n_all}")
        print(f"caption score ≥ 0.3 agrees with the labels on {agree}/{n_lab} sampled frames")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("mode", choices=["sheets", "run"])
    ap.add_argument("--step", type=float, default=2.0)
    a = ap.parse_args()
    cmd_sheets(a.data_dir, a.step) if a.mode == "sheets" else cmd_run(a.data_dir)


if __name__ == "__main__":
    main()
