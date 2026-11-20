"""CTC log-probabilities of the alignment audio (spec 5.5-2).

`LogProbs` is what the acoustic model produces and what the aligner consumes: one row per
model frame (about 20 ms), one column per vocabulary token (Hangul syllables, `|` = word gap).
Times are seconds from the start of the source file, like every other signal (scene cuts,
silences).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class LogProbs:
    lpz: np.ndarray  # (frames, vocab) log-softmax
    frame_sec: float  # seconds per frame
    offset: float  # time of frame 0 (seconds from file start)
    vocab: list[str]  # token for each column
    blank: int  # CTC blank (pad) column

    @property
    def duration(self) -> float:
        return self.lpz.shape[0] * self.frame_sec

    @property
    def end(self) -> float:
        return self.offset + self.duration

    def frame(self, t: float) -> int:
        """Frame index for time t, clamped to the array."""
        return min(len(self.lpz), max(0, int((t - self.offset) / self.frame_sec)))

    def window(self, t0: float, t1: float) -> tuple[np.ndarray, float]:
        """Rows covering [t0, t1) and the time of the first returned row."""
        f0, f1 = self.frame(t0), self.frame(t1)
        return self.lpz[f0:f1], self.offset + f0 * self.frame_sec

    def syllables_between(self, t0: float, t1: float) -> int:
        """Syllables the model hears in [t0, t1] (greedy CTC decode). Music gives few or none."""
        if t1 <= t0:
            return 0
        ids = self.lpz[self.frame(t0) : self.frame(t1)].argmax(axis=1)
        n, prev = 0, None
        for k in ids:
            if k != self.blank and k != prev and self.vocab[k] != "|":
                n += 1
            prev = k
        return n

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        with open(tmp, "wb") as f:
            np.savez_compressed(
                f,
                lpz=self.lpz.astype(np.float32),
                frame_sec=self.frame_sec,
                offset_sec=self.offset,
                vocab=np.array(self.vocab),
                pad_id=self.blank,
            )
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> LogProbs:
        """Reads files written by `save` (same format as eval/poc/compute_logits.py)."""
        z = np.load(path)
        return cls(
            lpz=z["lpz"],
            frame_sec=float(z["frame_sec"]),
            offset=float(z["offset_sec"]),
            vocab=[str(v) for v in z["vocab"]],
            blank=int(z["pad_id"]),
        )
