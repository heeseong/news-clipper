"""Convert MARS-L print-outs (cue sheet PDF + script PDF) into API-shaped JSON.

Stopgap until MARS-L API access is available. Produces files shaped like the
IF_MARSL_002 / IF_MARSL_003 responses so the rest of the pipeline can be built
and tested against them, plus `pdf_extra.json` with fields that the print-out
has but the API spec does not (형식 R/S, 예정 길이, 합계).

Usage:
    python eval/tools/marsl_pdf_export.py CUESHEET.pdf SCRIPTS.pdf OUT_DIR

Notes on fidelity:
- Script lines in the PDF are visually wrapped at the column width. A line that
  nearly reaches the right edge and does not end with punctuation is treated
  as wrapped and glued to the next one (Korean text wraps mid-word), with a
  space only when the next line is indented. Other breaks stay as "\\n".
  This is a heuristic: some spaces get lost and a few manual breaks get
  glued. The real API text should not need this.
- Left-column notes are exported as `memo`, one block per vertical group.
- `remark` is taken from the 비고 column. Whether the API's `remark` carries
  the same value is an assumption to verify.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

import pdfplumber

logging.disable(logging.WARNING)  # pdfplumber font warnings

BODY_X0 = 148.0  # left edge of the script column
TOP = 95.0
BOTTOM = 810.0
GRID_FONT_SIZE = 9.0  # the 04..24 hour grid on every page
FULL_LINE_X1 = 485.0  # body line reaching this x may have been wrapped
NOTE_FULL_LINE_X1 = 138.0
PARAGRAPH_GAP = 45.0  # >1 row apart means a blank row (paragraph break)
FIRST_ROW_MAX_TOP = 135.0  # text in the first row: continues the previous page
NOTE_BLOCK_GAP = 14.0


def _lines(region):
    return region.extract_text_lines(y_tolerance=6, x_tolerance=1.5)


def _is_wrapped(line, nxt, full_x1: float) -> bool:
    """Guess whether `line` was visually wrapped onto `nxt` by the editor."""
    text = line["text"].rstrip()
    if "◀" in text:
        # Speaker lines: wrapped only when a short tail (rest of the title) follows.
        return nxt["x1"] < 320 and not re.search(r"[.?!\"”]$", nxt["text"].strip())
    return line["x1"] >= full_x1 and not re.search(r"[.?!,\"”’'/]$", text)


def _glue(prev, nxt) -> str:
    # A leading space survives as an indented x0 (e.g. 160.4 vs 155.6).
    return " " if nxt["x0"] - prev["x0"] > 2.5 else ""


def _join(lines, full_x1: float, gap: float, sep: str) -> str:
    """Join PDF lines into logical text. Wrapped lines are glued together."""
    out: list[str] = []
    prev = None
    for ln in lines:
        text = ln["text"].strip()
        if prev is None:
            out.append(text)
        elif ln["top"] - prev["top"] > gap:
            out.append(sep + text)
        elif _is_wrapped(prev, ln, full_x1):
            out.append(_glue(prev, ln) + text)
        else:
            out.append("\n" + text)
        prev = ln
    return "".join(out)


def read_cuesheet(path: Path) -> dict:
    page = pdfplumber.open(path).pages[0]
    head = page.extract_text() or ""
    m_prog = re.search(r"^\s*(\S.*?)\s*\((\d{4}-\d{2}-\d{2})\)\s*$", head, re.M)
    m_conf = re.search(r"확정일시:\s*([\d-]+ [\d:]+)", head)
    m_ver = re.search(r"판\s*수:\s*(\d+)", head)
    rows = page.extract_table()
    header, body = rows[0], rows[1:]
    idx = {name: header.index(name) for name in ("No", "형식", "시간", "합계", "제목", "기자", "비고")}
    items = []
    for r in body:
        def cell(name: str) -> str:
            return (r[idx[name]] or "").replace("\n", "").strip()

        items.append(
            {
                "no": int(cell("No")),
                "format": cell("형식") or None,
                "duration": cell("시간"),
                "cumulative": cell("합계"),
                "title": cell("제목"),
                "reporter": cell("기자") or None,
                "remark": cell("비고") or None,
            }
        )
    return {
        "programName": m_prog.group(1) if m_prog else None,
        "broadcastDate": m_prog.group(2) if m_prog else None,
        "cueSheetVersion": int(m_ver.group(1)) if m_ver else None,
        "confirmedAt": m_conf.group(1) if m_conf else None,
        "items": items,
    }


def read_scripts(path: Path) -> list[dict]:
    """Return one dict per script (consecutive pages with the same header)."""
    scripts: list[dict] = []
    for page in pdfplumber.open(path).pages:
        page = page.filter(
            lambda o: not (o.get("object_type") == "char" and abs(o.get("size", 0) - GRID_FONT_SIZE) < 0.3)
        )
        title = " ".join(ln["text"] for ln in _lines(page.crop((0, 33, page.width, 62)))).strip()
        header = " ".join(ln["text"] for ln in _lines(page.crop((0, 62, page.width, 92)))).strip()
        body = _lines(page.crop((BODY_X0, TOP, page.width, BOTTOM)))
        notes = _lines(page.crop((25, TOP, BODY_X0, BOTTOM)))
        key = (title, header)
        if not scripts or scripts[-1]["key"] != key:
            m_rep = re.match(r"^(\S+?)\((\S+?)\)", header)
            m_out = re.search(r"출고:\s*([\d-]+ [\d:]+)", header)
            m_len = re.search(r"길이:\s*([\d:]+)", header)
            scripts.append(
                {
                    "key": key,
                    "title": title,
                    "reporter": m_rep.group(1) if m_rep else None,
                    "department": m_rep.group(2) if m_rep else None,
                    "releasedAt": m_out.group(1) if m_out else None,
                    "length": m_len.group(1) if m_len else None,
                    "pages": [],
                }
            )
        scripts[-1]["pages"].append({"body": body, "notes": notes})

    for s in scripts:
        # Body text continues across pages; a page break is not a paragraph break.
        s["content"] = _join_pages(s["pages"])
        s["memo"] = "\n\n".join(
            _join(p["notes"], NOTE_FULL_LINE_X1, NOTE_BLOCK_GAP, "\n\n") for p in s["pages"] if p["notes"]
        )
        del s["key"], s["pages"]
    return scripts


def _join_pages(pages: list[dict]) -> str:
    """Join pages; a page break is a paragraph break unless a line was wrapped."""
    text = ""
    prev_last = None
    for p in pages:
        if not p["body"]:
            continue
        chunk = _join(p["body"], FULL_LINE_X1, PARAGRAPH_GAP, "\n\n")
        if not text:
            text = chunk
        elif p["body"][0]["top"] > FIRST_ROW_MAX_TOP:
            text += "\n\n" + chunk  # page starts with a blank row
        elif _is_wrapped(prev_last, p["body"][0], FULL_LINE_X1):
            text += _glue(prev_last, p["body"][0]) + chunk
        else:
            text += "\n" + chunk
        prev_last = p["body"][-1]
    return text


def export(cuesheet_pdf: Path, scripts_pdf: Path, out_dir: Path) -> None:
    cue = read_cuesheet(cuesheet_pdf)
    scripts = read_scripts(scripts_pdf)
    if len(scripts) != len(cue["items"]):
        raise SystemExit(f"cue sheet has {len(cue['items'])} items but scripts PDF has {len(scripts)}")

    data, extra = [], []
    for item, script in zip(cue["items"], scripts):
        short = item["title"].rstrip("…").strip()
        if short and not script["title"].replace(" ", "").startswith(short.replace(" ", "")[:8]):
            print(f"warning: item {item['no']} title mismatch: {item['title']!r} vs {script['title']!r}")
        data.append(
            {
                "cueSheetItemTitle": item["title"],
                "cueSheetItemContent": None,
                "cueSheetItemOrderNumber": str(item["no"]),
                "createUserName": script["reporter"],
                "article": {
                    "articleTitle": script["title"],
                    "articleContent": script["content"],
                    "articleCategoryName": None,
                    "broadCastProgramName": cue["programName"],
                    "memo": script["memo"] or None,
                    "reporterName": script["reporter"] or item["reporter"],
                    "reporterEmailAddress": None,
                    "inputDateTime": script["releasedAt"],
                    "updateDatetime": script["releasedAt"],
                },
                "remark": item["remark"],
            }
        )
        extra.append(
            {
                "order": item["no"],
                "format": item["format"],
                "plannedDuration": item["duration"],
                "cumulative": item["cumulative"],
                "scriptLength": script["length"],
                "department": script["department"],
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "status": "OK",
        "success": True,
        "data": {
            "cueSheetId": None,
            "programName": cue["programName"],
            "cueSheetVersion": cue["cueSheetVersion"],
            "broadcastDate": cue["broadcastDate"],
            "inputDateTime": cue["confirmedAt"],
        },
    }
    (out_dir / "cuesheet.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (out_dir / "items.json").write_text(
        json.dumps({"status": "OK", "success": True, "data": data}, ensure_ascii=False, indent=2)
    )
    (out_dir / "pdf_extra.json").write_text(json.dumps(extra, ensure_ascii=False, indent=2))
    print(f"wrote {len(data)} items to {out_dir}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    export(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
