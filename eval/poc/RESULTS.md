# Alignment PoC results

## 2026-11-11 뉴스데스크 (golden set #1)

Recording: 20:04, 320x240, 29.97 fps, mono. The program starts at ~1:52 after
another news segment (title 1:59.9, headlines until 2:50, first anchor shot 2:50.4).
The recording ends before the program does (items 15+ are not in it).

Model: `kresnik/wav2vec2-large-xlsr-korean` (Hangul-syllable CTC), CPU, 4 threads.
Log-probs for 18.4 min of audio took 172 s (~6.4x real time).
Alignment: `ctc-segmentation`, item by item along the rundown, starting from
100 s with no explicit program-start detection; the headline section was skipped
correctly.

| Report | Lead start vs speech | Sign-off end vs speech | Clip start vs anchor cut | Clip end vs next cut |
|---|---|---|---|---|
| 2 | -0.48 s | +1.82 s | +1 frame | -½ frame |
| 3 | -0.48 s | -0.41 s | +1 frame | -½ frame |
| 4 | -0.49 s | +1.48 s | +1 frame | -½ frame |
| 6 | +0.04 s | +0.02 s | +1 frame | -½ frame |

(Speech start/end references come from silencedetect and are approximate, about ±0.3 s.)

Findings
- Lead start alignment is reliable. Snapping to the anchor-shot cut just before it
  gives the exact clip start.
- The sign-off end can overrun into the next item by up to ~2 s (CTC segmentation
  lets a window's last sentence absorb trailing audio). Ending the clip at the
  next item's anchor-shot cut, found from the next item's lead start, is exact.
- Scene threshold 0.3 missed 2 of 8 anchor cuts; 0.1 found all of them.
- The detected cut frame can be a one-frame blend: start = cut + 1 frame,
  end = cut - ½ frame.
- Frame-accurate rendering needs an output-side duration (`-ss A -i IN -t D`);
  input-side `-to` kept one extra frame.
- Sentence scores for correct alignments ranged from -1.2 to -4.0. A
  LOW_CONFIDENCE threshold needs negative examples (deliberately wrong scripts)
  before it can be set.

Reproduce (data under eval/data/ is not committed):

```bash
python eval/poc/compute_logits.py DATA/align.wav DATA/lpz_kresnik.npz --start 100
ffmpeg -i DATA/recording.mp4 -an -vf "select='gt(scene,0.1)',showinfo" -f null - 2>&1 \
  | grep -o "pts_time:[0-9.]*" | cut -d: -f2 > DATA/scenes_ff10.txt
python eval/poc/align_poc.py DATA
```
