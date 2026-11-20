"""Synthetic CTC log-probabilities: "the model heard these words at these times".

Tests never load the real acoustic model. `speech_logprobs` builds a LogProbs whose greedy decode
is exactly the given speech, so the aligner and the boundary rules can be checked against known
times.
"""

from __future__ import annotations

import numpy as np

from app.align.korean_text import to_spoken_hangul
from app.align.logprobs import LogProbs

FRAME = 0.02
SYLLABLE = 0.2  # seconds per spoken syllable
GAP = 0.06  # word gap
HIGH, LOW = -0.05, -12.0
BLANK, SPACE = "<pad>", "|"
# Syllables used for unrelated speech (another newscast, a greeting).
FILLER = "가나다라마바사아자차카타파하"


def vocab_for(*texts: str) -> list[str]:
    chars = sorted({ch for t in texts for ch in to_spoken_hangul(t) if ch != " "} | set(FILLER))
    return [BLANK, SPACE, *chars]


def speech_logprobs(duration: float, speech: list[tuple[float, str]], vocab: list[str]) -> LogProbs:
    """`speech`: (start time, text) — the text is spoken from that time, syllable by syllable.

    Returns the LogProbs plus nothing else; each utterance's syllable times follow from SYLLABLE/GAP.
    """
    n = int(round(duration / FRAME))
    index = {v: i for i, v in enumerate(vocab)}
    lpz = np.full((n, len(vocab)), LOW, dtype=np.float32)
    lpz[:, index[BLANK]] = HIGH
    for start, text in speech:
        t = start
        for word in to_spoken_hangul(text).split():
            for ch in word:
                f0 = int(t / FRAME)
                if f0 + 3 > n:
                    break
                lpz[f0 : f0 + 3, index[ch]] = HIGH
                lpz[f0 : f0 + 3, index[BLANK]] = LOW
                t += SYLLABLE
            # Word gap: blank (ctc-segmentation maps the `|` separator to blank).
            t += GAP
    return LogProbs(lpz=lpz, frame_sec=FRAME, offset=0.0, vocab=vocab, blank=index[BLANK])


def spoken_seconds(text: str) -> float:
    words = to_spoken_hangul(text).split()
    return sum(len(w) for w in words) * SYLLABLE + len(words) * GAP


def silent_logprobs(duration: float) -> LogProbs:
    """Nothing but silence (blank everywhere)."""
    return speech_logprobs(duration, [], vocab_for())
