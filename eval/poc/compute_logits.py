"""Compute CTC log-probabilities for a 16 kHz mono WAV with a wav2vec2 model.

Usage: python eval/poc/compute_logits.py AUDIO.wav OUT.npz [--model ID] [--start S] [--end E]
Saves lpz (frames x vocab, log-softmax), frame_sec, offset_sec, vocab list.
"""
import argparse, json, time
import numpy as np, soundfile as sf, torch
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

ap = argparse.ArgumentParser()
ap.add_argument("audio"); ap.add_argument("out")
ap.add_argument("--model", default="kresnik/wav2vec2-large-xlsr-korean")
ap.add_argument("--start", type=float, default=0.0); ap.add_argument("--end", type=float, default=None)
ap.add_argument("--chunk", type=float, default=20.0); ap.add_argument("--context", type=float, default=1.0)
a = ap.parse_args()
torch.set_num_threads(4)
proc = Wav2Vec2Processor.from_pretrained(a.model)
model = Wav2Vec2ForCTC.from_pretrained(a.model).eval()
wav, sr = sf.read(a.audio, dtype="float32"); assert sr == 16000
s0 = int(a.start * sr); s1 = int(a.end * sr) if a.end else len(wav)
wav = wav[s0:s1]
step, ctx = int(a.chunk * sr), int(a.context * sr)
outs, t = [], time.time()
for i in range(0, len(wav), step):
    lo, hi = max(0, i - ctx), min(len(wav), i + step + ctx)
    x = proc(wav[lo:hi], sampling_rate=sr, return_tensors="pt").input_values
    with torch.inference_mode():
        lp = torch.log_softmax(model(x).logits[0], dim=-1).numpy()
    fps = lp.shape[0] / ((hi - lo) / sr)
    a0 = int(round((i - lo) / sr * fps)); a1 = a0 + int(round(min(step, len(wav) - i) / sr * fps))
    outs.append(lp[a0:a1])
    print(f"{(i+step)/sr:7.1f}s / {len(wav)/sr:.1f}s  elapsed {time.time()-t:.0f}s", flush=True)
lpz = np.concatenate(outs)
vocab = [k for k, _ in sorted(proc.tokenizer.get_vocab().items(), key=lambda kv: kv[1])]
np.savez_compressed(a.out, lpz=lpz, frame_sec=len(wav) / sr / lpz.shape[0], offset_sec=a.start,
                    vocab=np.array(vocab), pad_id=proc.tokenizer.pad_token_id)
print("done", lpz.shape, "frame_sec", len(wav) / sr / lpz.shape[0])
