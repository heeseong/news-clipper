"""wav2vec2 CTC acoustic model (spec 5.5-2): alignment audio → LogProbs.

Runs locally on CPU; audio never leaves the server. The model is loaded once per worker process
and kept in memory (the `align` worker runs one task at a time).

Needs the `model` extra (torch, transformers, soundfile). Imported lazily so the API and the
other workers do not need PyTorch.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from .logprobs import LogProbs

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
_lock = threading.Lock()
_loaded: dict[str, tuple[Any, Any]] = {}


class ModelUnavailable(Exception):
    """PyTorch/transformers are not installed or the model cannot be loaded."""


def load_model(model_id: str) -> tuple[Any, Any]:
    with _lock:
        if model_id not in _loaded:
            # Plain HTTP download: the Xet protocol is blocked on some networks (CLAUDE.md).
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            try:
                from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
            except ImportError as e:
                raise ModelUnavailable(
                    "정렬 모델 패키지(torch, transformers)가 설치돼 있지 않습니다 (pip install '.[model]')"
                ) from e
            started = time.monotonic()
            try:
                processor = Wav2Vec2Processor.from_pretrained(model_id)
                model = Wav2Vec2ForCTC.from_pretrained(model_id).eval()
            except OSError as e:
                raise ModelUnavailable(f"정렬 모델을 불러오지 못했습니다 ({model_id}): {e}") from e
            _loaded[model_id] = (processor, model)
            log.info("alignment model loaded", extra={"model": model_id, "seconds": round(time.monotonic() - started, 1)})
        return _loaded[model_id]


def compute_logprobs(
    wav: Path,
    model_id: str,
    *,
    chunk_seconds: float = 20.0,
    context_seconds: float = 1.0,
    threads: int = 4,
    on_progress: Callable[[float], None] | None = None,
) -> LogProbs:
    """Log-probabilities for a 16 kHz mono WAV, computed in chunks with a little context on each
    side so chunk edges are not cut mid-syllable (same method as eval/poc/compute_logits.py)."""
    import soundfile as sf
    import torch

    processor, model = load_model(model_id)
    torch.set_num_threads(threads)
    audio, sr = sf.read(str(wav), dtype="float32")
    if sr != SAMPLE_RATE:
        raise ValueError(f"정렬용 오디오는 16 kHz여야 합니다 ({sr} Hz)")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    step, ctx = int(chunk_seconds * sr), int(context_seconds * sr)
    parts: list[np.ndarray] = []
    for i in range(0, len(audio), step):
        lo, hi = max(0, i - ctx), min(len(audio), i + step + ctx)
        x = processor(audio[lo:hi], sampling_rate=sr, return_tensors="pt").input_values
        with torch.inference_mode():
            lp = torch.log_softmax(model(x).logits[0], dim=-1).numpy()
        fps = lp.shape[0] / ((hi - lo) / sr)
        a0 = int(round((i - lo) / sr * fps))
        a1 = a0 + int(round(min(step, len(audio) - i) / sr * fps))
        parts.append(lp[a0:a1])
        if on_progress:
            on_progress(min(1.0, (i + step) / len(audio)))
    lpz = np.concatenate(parts) if parts else np.zeros((0, 1), dtype=np.float32)
    vocab = [k for k, _ in sorted(processor.tokenizer.get_vocab().items(), key=lambda kv: kv[1])]
    frame_sec = len(audio) / sr / max(1, lpz.shape[0])
    return LogProbs(lpz=lpz, frame_sec=frame_sec, offset=0.0, vocab=vocab, blank=processor.tokenizer.pad_token_id)
