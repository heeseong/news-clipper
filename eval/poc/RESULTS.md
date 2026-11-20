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

## 2026-11-02 뉴스데스크 (golden set #2)

Recording: 17:21, same format. It starts directly with the title (no preceding
news). Different (male) anchor. 27 rundown items; the cue sheet print-out spans
two pages. Reports: 2, 3, 5, 6 (6 is a 광주MBC package). Reference cuts were
labelled frame by frame before running the aligner. Log-probs: 228 s for 17.4 min.

First run (method as of golden set #1): 2 of 4 reports correct at both ends.

| Problem | Cause | Fix |
|---|---|---|
| Report 2 start 3.4 s after the anchor cut | Anchor greeted viewers (53.9-56.4 s) before the lead; greeting is not in the script | No cut right before the lead: start in the silence before the lead. Reference corrected to that point |
| Report 6 start 2.9 s early; report 5 end 3 s early | Anchor did not read the lead's first words as written; the aligner parked them in the 1.8 s silence before the lead (sentence score -2.9, not flagged) | If the first-character onset is inside a silence, move it to the silence end and flag `LEAD_ONSET_IN_SILENCE` |
| Leads ~0.5 s early (also on #1) | ctc-segmentation pads sentence bounds (first char - 0.5 s) and lets the first sentence of a window absorb leftover audio | Use first/last character timestamps; align the previous item's last sentence as a guard |
| `KIA` read as 케이아이에이 | Spelled letter by letter | Pronunciation dictionary (KIA -> 기아, …) |
| `[영상] 홈런` kept as a sentence | 광주MBC script uses bracket directives | Parser strips `[...]` |

After the fixes, both golden sets:

| Broadcast | Report | Clip start | Clip end |
|---|---|---|---|
| 11-02 | 2 | lead silence (greeting skipped) | next anchor cut -½ frame |
| 11-02 | 3, 5 | anchor cut +1 frame | next anchor cut -½ frame |
| 11-02 | 6 | anchor cut +1 frame (flag: LEAD_ONSET_IN_SILENCE) | next anchor cut -½ frame |
| 11-11 | 2, 3, 4, 6 | anchor cut +1 frame | next anchor cut -½ frame |

16/16 boundaries match the reference. On 11-11, lead onsets are now within
0.02 s of the speech start for 3 of 4 reports (+0.54 s for one).
Caveat: 11-02 is no longer an unseen test; the fixes were made after seeing it.

### Wrong-script test (detecting misalignment)

Each 11-02 report window aligned with its own script vs. each 11-11 report script:

| | Median sentence score | Sentences below -5 |
|---|---|---|
| Correct script (4) | -2.7 to -3.1 | 0-10% |
| Wrong script (16) | -7.2 to -10.8 | 88-100% |

The minimum sentence score does not separate them (a correct script reached
-9.6 on a mis-transcribed interview line). Proposed rule: `LOW_CONFIDENCE`
when the median is below -5 or more than 30% of sentences are below -5.

## 2026-11-03 큐시트 + 2026-11-04 녹화본 (mismatched pair)

The supplied recording (`…20261104…mp4`) was the 11-04 broadcast; its headlines
and reports do not appear in the 11-03 cue sheet. Used as a real-world test of
mismatch detection, with no tuning.

| Item | Median score | Below -5 | Aligned / planned length | Flags |
|---|---|---|---|---|
| R2 | -4.30 | 32% | 0.34 | LOW_CONFIDENCE, DURATION_MISMATCH |
| R3 | -8.11 | 100% | 0.21 | LOW_CONFIDENCE, DURATION_MISMATCH |
| R4 | -7.50 | 96% | 0.48 | LOW_CONFIDENCE, DURATION_MISMATCH |
| R5 | -8.25 | 100% | 0.40 | LOW_CONFIDENCE, DURATION_MISMATCH |
| S18 | -3.62 | 0% | ~0.5 | DURATION_MISMATCH only (aligned over a recap card) |

- All 4 reports were rejected; no wrong clip would reach upload.
- The score rule alone was marginal for R2 (32% vs. the 30% limit) and missed
  S18. The length ratio separates far better: correct reports on 11-02/11-11
  are 0.98-1.04 of the planned length, mismatched ones 0.21-0.48.
- Both checks together: 0 false accepts among reports here, 0 false rejects
  among the 7 correct reports of 11-02/11-11 (short briefs at the very end of
  a truncated recording are flagged, which is correct).

## 2026-11-04 뉴스데스크 (golden set #3, blind)

Same recording as the mismatched pair above, now with its own cue sheet.
~110 s of another newscast, title, headlines, then a 65 s script-less bridge
VCR (의대 타임라인 브릿지) before the first report. 5 reports (3, 4, 6, 7, 8);
report 3 opens with an anchor greeting. Reference cuts labelled before running.

Blind run (code unchanged since golden set #2): **8/10 boundaries correct**.
Report 3 was placed over the headlines/bridge (172-228 s instead of 239-344 s)
but flagged DURATION_MISMATCH, so no wrong clip would have been produced.

Cause: when a window starts well before the item, ctc-segmentation crams the
whole text into the audio that comes first (727 characters into 31 s). Starting
the window at 230 s gives the right answer. Sentence scores did not show it;
the duration check did. The headlines also repeat the lead's first sentence
almost verbatim.

Fix: when an alignment fails the plausibility checks (LOW_CONFIDENCE or
DURATION_MISMATCH), retry with the window starting at each later scene cut and
keep the first plausible result. Script-less items (title, bridge) also widen
the next window by their planned length.

After the fix: 10/10 on 11-04 (report 3 starts in the silence after the
greeting, 238.993 s vs. 238.99 s), no change on 11-02 and 11-11, and the
mismatched pair is still fully rejected (the retries did not produce a false
accept).

## 2026-11-05 뉴스데스크 (golden set #4, blind)

~125 s of another newscast, title, headlines, studio wide shot, then the first
report; the anchor's greeting starts over the wide shot. 5 reports (2, 3, 5, 6, 7).
Reference labelled before running; code unchanged since golden set #3.

Blind run: **9/10 boundaries correct, 1 wrong and not flagged.** Report 2's lead
onset was exact (188.45 s), but the clip start snapped to the anchor cut 1.8 s
earlier, which falls inside the greeting, so the clip would open with ~1.3 s of
"…뉴스데스크입니다".

Fix: after choosing the anchor cut, check whether the acoustic model hears speech
(>= 4 syllables) between the cut and the silence before the lead; if so it is a
greeting, so start in that silence instead. A loudness-only check was tried first
and is not enough (music would count as speech).

This also showed that **11-11 report 2 had the same greeting tail** ("…뉴스…습니다"
between the cut and the lead); its reference had been set to the anchor cut before
the greeting rule existed and was corrected to the silence before the lead.

### Totals (after all fixes)

| Golden set | Upload reports | Boundaries correct | First run |
|---|---|---|---|
| 11-11 | 4 | 8/8 | 8/8 (reference later corrected for a greeting tail) |
| 11-02 | 3 (+1 other-station, excluded) | 6/6 | 2 of 4 reports wrong; fixed after seeing it |
| 11-04 | 5 | 10/10 | blind 8/10, both errors flagged |
| 11-05 | 5 | 10/10 | blind 9/10, 1 error not flagged (greeting) |
| 11-03 cue + 11-04 video | 4 | all rejected | mismatch detection |

Every broadcast so far opened its first report with an anchor greeting; that is
the case that broke the rules most often.

## Real MARS-L API scripts (2026-11-04)

The 11-04 golden set was re-run with the real IF_MARSL_003 response instead of
scripts converted from the PDF print-out (no title/bridge items in the API, so
item numbers shift by 2). Planned lengths came from the API
(`ancMentCttTime + artclCttTime + artclExtTime`, which matches the print-out's
시간 column for 22/22 items). Result: 10/10 boundaries, identical to the PDF run.

Reproduce (data under eval/data/ is not committed):

```bash
python eval/poc/compute_logits.py DATA/align.wav DATA/lpz_kresnik.npz --start 100
ffmpeg -i DATA/recording.mp4 -an -vf "select='gt(scene,0.1)',showinfo" -f null - 2>&1 \
  | grep -o "pts_time:[0-9.]*" | cut -d: -f2 > DATA/scenes_ff10.txt
python eval/poc/align_poc.py DATA
```

## 2026-11-16 뉴스데스크 (golden set #5, blind, service code)

First broadcast run through the service alignment (`backend/app/align`, M3) instead of the PoC
script, with the real MARS-L API cue sheet (#2087663, 25 items) and no tuning.
Recording 20:12, 320x240, 29.97 fps; it starts with ~98 s of another newscast, then headlines
(~98-160 s), and ends during item 14 (the cue sheet plans 22:15). 5 upload reports (1, 3, 4, 5, 6).
Log-probs: 252 s for 20.2 min of audio (4 cores).

Reference labelled before running (`ground_truth.json`): scene cuts checked on frames, lead and
sign-off positions from the model's greedy transcript (no script) and silencedetect. Report 1's
greeting ("…목포MBC 뉴스데스크입니다", 161.4-163.6 s) starts over the studio wide shot and the anchor
cut (162.46 s) falls inside it, as on 11-05: its reference is a range, the silence between the
greeting and the lead (163.606-164.582 s). `score_alignment.py` gained range references for this.

| Report | Clip start | Clip end | Verdict |
|---|---|---|---|
| 1 | 164.082 (in the silence after the greeting) | S2 anchor cut -½ frame | ok |
| 3, 4, 5, 6 | anchor cut +1 frame | next anchor cut -½ frame | ok |

**10/10 boundaries correct on the first (blind) run**; sentence-score medians -2.6 to -3.0 for the
reports. Distracting cuts next to the right ones were handled (292.8 inside the previous brief's
footage, 409.8 and 737.4 footage cuts around the sign-off).

Observations (briefs only, no clip affected; rules not changed):
- Brief 12: LOW_CONFIDENCE (median -4.2, 50 % below -5) on number-heavy sentences. `5.8%` is
  spelled 오점팔, the anchor reads [오쩜팔] (the model hears 쩜). A decimal-point reading rule may help.
- Brief 13 was placed in the last 10 s of the recording (flagged LOW_CONFIDENCE + DURATION_MISMATCH)
  though it is at 1127-1157 s; aligned alone in [1124, 1160] it scores -2.6 to -3.0. Its window
  runs past the program into ~25 s of unrelated audio at the end of the recording (1186-1212 s),
  and the text is crammed there: the same effect as the headline cramming of 11-04, at the end of
  the window. Brief 14 (in the recording, 1161-1183 s) became NOT_FOUND after it. The retry from
  later cuts does not help here because every retry window also reaches the end of the recording.
