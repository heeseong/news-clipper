"""Thumbnail candidates (spec 5.7, plan 4.4).

Frames are taken from the original (not the proxy) just after each scene cut inside the clip and at
even intervals, then scored:

- sharpness: variance of the Laplacian (log scale, relative to the sharpest frame of the clip)
- exposure: mid-grey brightness and some contrast score higher; near-black and flat frames lose
- face: a clearly visible face (YuNet) scores higher
- anchor shot: frames in the anchor's part of the clip lose (report pictures are preferred)
- caption (briefs only): a straight brief's thumbnail is a frame of the footage that shows the
  on-air caption (the title super at the bottom), so frames with one score much higher. Found by
  text-like strokes and a horizontal bar edge in the bottom band (no OCR)

The best frame of each shot comes first, so the top candidates are different pictures.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.media.analysis import DEINTERLACE
from app.media.ffmpeg import ProbeResult, run_ffmpeg

WIDTH, HEIGHT = 1280, 720
AFTER_CUT = 0.5  # seconds after a cut (past blended cut frames)
NEAR_CUT = 0.15  # frames this close to a cut may be blends
MIN_GAP = 0.25  # candidate times closer than this are the same frame
FACE_MODEL = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"

WEIGHTS = {"sharpness": 0.45, "exposure": 0.25, "face": 0.3, "anchor": -0.6, "dark": -1.0, "caption": 0.0}
WEIGHTS_BY_KIND = {"report": WEIGHTS, "brief": WEIGHTS | {"face": 0.15, "caption": 1.0}}
CAPTION_BAND = (0.70, 0.95)  # where the on-air title caption sits (share of the height)


def caption_score(gray: np.ndarray) -> float:
    """0..1: how much the bottom band looks like a caption bar with text over the picture."""
    import cv2

    h = gray.shape[0]
    band = gray[int(h * CAPTION_BAND[0]) : int(h * CAPTION_BAND[1])]
    mid = gray[int(h * 0.35) : int(h * 0.65)]
    gx_band = np.abs(cv2.Sobel(band, cv2.CV_32F, 1, 0, ksize=3))
    gx_mid = np.abs(cv2.Sobel(mid, cv2.CV_32F, 1, 0, ksize=3))
    gy_band = np.abs(cv2.Sobel(band, cv2.CV_32F, 0, 1, ksize=3))
    # Text: many sharp vertical strokes in the band, more than in the picture above it.
    text = float(np.clip(((gx_band > 120).mean() - (gx_mid > 120).mean()) * 6, 0, 1))
    # Bar: a horizontal edge across most of the width (the caption box's top or bottom).
    border = float(np.clip((gy_band > 80).mean(axis=1).max() / 0.6, 0, 1))
    return round(text * (0.4 + 0.6 * border), 3)


@dataclass
class Candidate:
    time: float
    shot: int  # index of the shot (between cuts) inside the clip
    in_anchor: bool
    path: Path | None = None
    score: float = 0.0
    details: dict[str, float] = field(default_factory=dict)


def anchor_end_for(
    kind: str, start: float, end: float, cuts: list[float], lead_end: float | None
) -> float:
    """End of the anchor's part of the clip.

    A report: the lead (its last sentence), extended to a cut close by (the report starts on a cut).
    A brief: the first shot (the anchor reads the rest over pictures, if any)."""
    inner = [c for c in cuts if start + 0.5 < c < end]
    if kind == "report" and lead_end is not None:
        near = [c for c in inner if abs(c - lead_end) <= 2.0]
        return min(near, key=lambda c: abs(c - lead_end)) if near else lead_end
    return inner[0] if inner else end


def candidate_times(
    start: float, end: float, cuts: list[float], anchor_end: float, samples: int = 12
) -> list[Candidate]:
    inner = sorted(c for c in cuts if start < c < end)
    edges = [start, *inner, end]

    def shot_of(t: float) -> int:
        return sum(1 for c in inner if c <= t)

    times = []
    for a, b in zip(edges, edges[1:], strict=False):
        if b - a >= 2 * NEAR_CUT:
            times.append(a + min(AFTER_CUT, (b - a) / 2))
    span = end - start
    times += [start + (i + 0.5) * span / samples for i in range(samples)]
    out: list[Candidate] = []
    for t in sorted(times):
        if any(abs(t - c) < NEAR_CUT for c in inner):
            continue
        if out and t - out[-1].time < MIN_GAP:
            continue
        out.append(Candidate(round(t, 3), shot_of(t), t < anchor_end))
    return out


def frame_filter(probe: ProbeResult) -> str:
    """Square pixels, fit in 1280x720 and pad (4:3 or odd sizes get bars)."""
    parts = [DEINTERLACE] if probe.interlaced else []
    parts += [
        "scale=trunc(iw*sar/2)*2:ih",
        "setsar=1",
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease",
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2",
    ]
    return ",".join(parts)


def extract_frame(src: Path, probe: ProbeResult, t: float, out: Path, timeout: float = 120) -> Path:
    """One frame of the original at `t` seconds from the file start (input seeking adds the
    file's start time, so this is the same axis as cuts and alignment)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        ["-ss", f"{t:.3f}", "-i", str(src), "-map", "0:v:0", "-vf", frame_filter(probe),
         "-frames:v", "1", "-q:v", "2", str(out)],
        timeout=timeout,
    )  # fmt: skip
    if not out.exists():
        raise FileNotFoundError(f"no frame at {t:.3f}s")
    return out


class FaceDetector:
    def __init__(self) -> None:
        import cv2

        self._detector = cv2.FaceDetectorYN.create(str(FACE_MODEL), "", (640, 360), 0.8)

    def largest_face(self, bgr: np.ndarray) -> float:
        """Share of the frame covered by the largest face (0 when none)."""
        import cv2

        small = cv2.resize(bgr, (640, 360))
        self._detector.setInputSize((640, 360))
        _, faces = self._detector.detect(small)
        if faces is None or len(faces) == 0:
            return 0.0
        return float(max(f[2] * f[3] for f in faces) / (640 * 360))


def measure(path: Path, faces: FaceDetector | None) -> dict[str, float]:
    import cv2

    bgr = cv2.imread(str(path))
    if bgr is None:
        raise ValueError(f"cannot read {path}")
    # Ignore letterbox / pillarbox bars.
    gray_full = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    rows = np.where(gray_full.max(axis=1) > 16)[0]
    cols = np.where(gray_full.max(axis=0) > 16)[0]
    if len(rows) and len(cols):
        bgr = bgr[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1]
    gray = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (320, 180))
    return {
        "laplacian": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        "brightness": float(gray.mean()),
        "contrast": float(gray.std()),
        "face_area": faces.largest_face(bgr) if faces else 0.0,
        "caption": caption_score(gray),
    }


def score(candidates: list[Candidate], measures: list[dict[str, float]], kind: str = "report") -> None:
    weights = WEIGHTS_BY_KIND.get(kind, WEIGHTS)
    top = max((math.log1p(m["laplacian"]) for m in measures), default=0.0) or 1.0
    for c, m in zip(candidates, measures, strict=True):
        sharp = math.log1p(m["laplacian"]) / top
        exposure = max(0.0, 1 - abs(m["brightness"] - 120) / 120) * min(1.0, m["contrast"] / 40)
        face = min(1.0, m["face_area"] / 0.04) if m["face_area"] else 0.0
        dark = 1.0 if m["brightness"] < 20 or m["contrast"] < 8 else 0.0
        parts = {"sharpness": sharp, "exposure": exposure, "face": face, "anchor": float(c.in_anchor), "dark": dark,
                 "caption": m.get("caption", 0.0)}  # fmt: skip
        c.score = round(sum(weights[k] * v for k, v in parts.items()), 4)
        c.details = {k: round(v, 3) for k, v in parts.items()} | {
            k: round(v, 2) for k, v in m.items() if k != "caption"
        }


def pick(candidates: list[Candidate], n: int) -> list[Candidate]:
    """The best frame of each shot first (by score), then the remaining frames by score."""
    ranked = sorted(candidates, key=lambda c: -c.score)
    firsts, rest, seen = [], [], set()
    for c in ranked:
        (rest if c.shot in seen else firsts).append(c)
        seen.add(c.shot)
    return (firsts + rest)[:n]


def find_candidates(
    src: Path,
    probe: ProbeResult,
    times: list[Candidate],
    work_dir: Path,
    n: int = 6,
    *,
    kind: str = "report",
    detect_faces: bool = True,
    threads: int = 4,
    on_progress: Callable[[float], None] | None = None,
) -> list[Candidate]:
    """Extract and score every candidate frame; returns the best `n` (frames in `work_dir`)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(times):
        c.path = work_dir / f"f{i:03d}.jpg"
    done = 0

    def grab(c: Candidate) -> None:
        nonlocal done
        extract_frame(src, probe, c.time, c.path)
        done += 1
        if on_progress:
            on_progress(done / max(1, len(times)))

    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(grab, times))
    faces = FaceDetector() if detect_faces and FACE_MODEL.exists() else None
    measures = [measure(c.path, faces) for c in times]
    score(times, measures, kind)
    return pick(times, n)


CAPTION_ON = 0.5  # caption score that counts as "the caption is on screen"
SCAN_STEP = 0.5
SCAN_SECONDS = 30.0  # a brief's anchor shot plus the caption's appearance fit in this
CAPTION_SETTLE = 0.5  # after the caption appears (it may still be fading or sliding in)
# Mean absolute difference (0..255, blurred 64x36 grey, upper 65% of the picture) from the first
# frame of the clip above which the anchor shot is over. 11-16 뉴스데스크: anchor shot 0–2,
# footage 23–100 (the cut to footage is often a dissolve that scene detection misses).
ANCHOR_DIFF = 15.0


def _anchor_view(path: Path) -> np.ndarray:
    import cv2

    gray = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2GRAY)
    rows = np.where(gray.max(axis=1) > 16)[0]
    cols = np.where(gray.max(axis=0) > 16)[0]
    if len(rows) and len(cols):
        gray = gray[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1]
    small = cv2.GaussianBlur(cv2.resize(gray, (64, 36)), (3, 3), 0).astype(np.float32)
    return small[:23]  # leave out the caption band and the sign-language inset


@dataclass
class BriefScan:
    footage_start: float | None  # when the picture stops being the anchor shot
    first_caption: Candidate | None


def scan_brief(src: Path, probe: ProbeResult, start: float, end: float, work_dir: Path) -> BriefScan:
    """A brief's thumbnail (2026-11-20, user decision): the first frame after the anchor shot on
    which the on-air title caption has come up.

    Every SCAN_STEP seconds from the clip start: the anchor shot is over when the picture differs
    from the first frame (ANCHOR_DIFF; captions on the anchor shot, e.g. a quote, are therefore
    ignored); then the first frame with a caption, taken CAPTION_SETTLE seconds later if the
    caption is still there."""
    work_dir.mkdir(parents=True, exist_ok=True)
    stop = min(end - NEAR_CUT, start + SCAN_SECONDS)
    t = start + AFTER_CUT
    if t >= stop:
        return BriefScan(None, None)
    ref = _anchor_view(extract_frame(src, probe, t, work_dir / "ref.jpg"))
    footage_start = None
    i = 0
    t += SCAN_STEP
    while t < stop:
        path = extract_frame(src, probe, t, work_dir / f"scan{i:02d}.jpg")
        if footage_start is None:
            if float(np.abs(_anchor_view(path) - ref).mean()) < ANCHOR_DIFF:
                t += SCAN_STEP
                i += 1
                continue
            footage_start = round(t, 3)
        m = measure(path, None)
        if m["caption"] >= CAPTION_ON:
            if t + CAPTION_SETTLE < end - NEAR_CUT:
                settled = extract_frame(src, probe, t + CAPTION_SETTLE, work_dir / f"scan{i:02d}s.jpg")
                ms = measure(settled, None)
                if ms["caption"] >= CAPTION_ON:
                    t, path, m = t + CAPTION_SETTLE, settled, ms
            c = Candidate(round(t, 3), -1, False, path)
            c.details = {"rule": 1.0, "caption": m["caption"]} | {
                k: round(v, 2) for k, v in m.items() if k != "caption"
            }
            return BriefScan(footage_start, c)
        t += SCAN_STEP
        i += 1
    return BriefScan(footage_start, None)


def choose(
    src: Path,
    probe: ProbeResult,
    kind: str,
    start: float,
    end: float,
    cuts: list[float],
    lead_end: float | None,
    work_dir: Path,
    n: int = 6,
    samples: int = 12,
    on_progress: Callable[[float], None] | None = None,
) -> list[Candidate]:
    """The candidates of one clip, best first (frames in `work_dir`).

    A report: the best frames by score. A brief: the first footage frame with the on-air title
    caption (scan_brief), then the best frames by score as alternatives."""
    anchor_end = anchor_end_for(kind, start, end, cuts, lead_end)
    scan = scan_brief(src, probe, start, end, work_dir / "scan") if kind == "brief" else None
    if scan and scan.footage_start is not None:
        anchor_end = scan.footage_start  # the change of picture, also when the cut is a dissolve
    times = candidate_times(start, end, cuts, anchor_end, samples)
    picked = find_candidates(src, probe, times, work_dir, n, kind=kind, on_progress=on_progress)
    if not scan or scan.first_caption is None:
        return picked
    first = scan.first_caption
    first.score = max((c.score for c in picked), default=0.0)  # shown first; the score is for display
    return [first] + [c for c in picked if abs(c.time - first.time) >= MIN_GAP][: n - 1]
