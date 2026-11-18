# eval

Tools for checking the pipeline against real broadcasts. Real material
(recordings, cue sheets, scripts) lives in `eval/data/`, which is git-ignored.

## Cue sheet and script (MARS-L)

Until MARS-L API access is available, cue sheets and scripts come from the
MARS-L print-outs (PDF).

```bash
# 1. PDF print-outs -> API-shaped JSON (cuesheet.json, items.json, pdf_extra.json)
python eval/tools/marsl_pdf_export.py CUESHEET.pdf SCRIPTS.pdf eval/data/2026-11-11-newsdesk

# 2. Parse with the backend parser and write a review report (review.md)
python eval/tools/inspect_rundown.py eval/data/2026-11-11-newsdesk
```

`items.json` follows the IF_MARSL_003 response shape. `pdf_extra.json` holds
what the print-out has but the API spec does not (형식 R/S, 예정 길이, 합계);
`inspect_rundown.py` uses it as ground truth for the R/S classification.

The PDF converter guesses where the editor wrapped lines (see its docstring),
so some spaces are lost at wrap points. This does not affect sentence or
section boundaries.

Requires `pdfplumber` (PDF converter only) and `pydantic`.
