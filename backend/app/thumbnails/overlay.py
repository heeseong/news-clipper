"""Thumbnail text overlay (spec 5.7, plan 4.5): 1280x720 JPG of at most 2 MB.

The design comes from the program profile's `thumbnail_template` (JSON); keys left out use
DEFAULT_TEMPLATE. The text is the clip's thumbnail text; a line break in it is kept, otherwise
it is wrapped to fit and the font shrinks down to `min_font_size`.

    {
      "font": "fonts/MyFont.ttf",          # under STORAGE_ROOT/branding/ or absolute; default: bundled NanumGothic ExtraBold
      "font_size": 104, "min_font_size": 64, "max_lines": 2,
      "position": "bottom",                  # top | center | bottom
      "align": "center",                     # left | center | right
      "margin": 56,
      "text_color": "#FFFFFF", "stroke_color": "#000000", "stroke_width": 7,
      "shade": 0.55,                         # dark gradient behind the text (0 = none)
      "logo": "logo.png", "logo_position": "top-right", "logo_height": 72
    }
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.core.config import get_settings

WIDTH, HEIGHT = 1280, 720
MAX_BYTES = 2 * 1024 * 1024
FONT = Path(__file__).parent / "fonts" / "NanumGothic-ExtraBold.ttf"

DEFAULT_TEMPLATE: dict[str, Any] = {
    "font": None,
    "font_size": 104,
    "min_font_size": 64,
    "max_lines": 2,
    "position": "bottom",
    "align": "center",
    "margin": 56,
    "line_spacing": 1.15,
    "text_color": "#FFFFFF",
    "stroke_color": "#000000",
    "stroke_width": 7,
    "shade": 0.55,
    "logo": None,
    "logo_position": "top-right",
    "logo_height": 72,
}


def branding_dir() -> Path:
    return get_settings().storage_root / "branding"


def template_with_defaults(template: dict[str, Any] | None) -> dict[str, Any]:
    return DEFAULT_TEMPLATE | {k: v for k, v in (template or {}).items() if k in DEFAULT_TEMPLATE}


def _asset(name: str | None) -> Path | None:
    if not name:
        return None
    p = Path(name)
    p = p if p.is_absolute() else branding_dir() / p
    return p if p.exists() else None


def _font(template: dict[str, Any], size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_asset(template["font"]) or FONT), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for para in text.splitlines() or [""]:
        words = para.split()
        line = ""
        for w in words:
            trial = f"{line} {w}".strip()
            if draw.textlength(trial, font=font) <= max_width or not line:
                line = trial
            else:
                lines.append(line)
                line = w
        lines.append(line)
    return [ln for ln in lines if ln]


def layout(text: str, template: dict[str, Any]) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Largest font size at which the text fits in `max_lines` lines (or the smallest size)."""
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    max_width = WIDTH - 2 * template["margin"]
    size = template["font_size"]
    while True:
        font = _font(template, size)
        lines = _wrap(draw, text, font, max_width)
        fits = len(lines) <= template["max_lines"] and all(draw.textlength(ln, font=font) <= max_width for ln in lines)
        if fits or size <= template["min_font_size"]:
            return font, lines
        size -= 4


def _shade(img: Image.Image, top: int, bottom: int, strength: float, fade_down: bool) -> None:
    """Dark gradient over rows [top, bottom): strongest at the image edge."""
    h = max(1, bottom - top)
    alpha = Image.new("L", (1, h))
    for y in range(h):
        f = y / h if fade_down else 1 - y / h
        alpha.putpixel((0, y), int(255 * strength * f))
    alpha = alpha.resize((WIDTH, h))
    black = Image.new("RGB", (WIDTH, h), "black")
    region = img.crop((0, top, WIDTH, bottom))
    img.paste(Image.composite(black, region, alpha), (0, top))


def render(frame: Path, text: str, template: dict[str, Any] | None = None) -> Image.Image:
    t = template_with_defaults(template)
    img = Image.open(frame).convert("RGB")
    if img.size != (WIDTH, HEIGHT):
        img = img.resize((WIDTH, HEIGHT))
    text = text.strip()
    if text:
        font, lines = layout(text, t)
        ascent, descent = font.getmetrics()
        line_h = int((ascent + descent) * t["line_spacing"])
        block_h = line_h * len(lines)
        if t["position"] == "top":
            y0 = t["margin"]
        elif t["position"] == "center":
            y0 = (HEIGHT - block_h) // 2
        else:
            y0 = HEIGHT - t["margin"] - block_h
        if t["shade"]:
            if t["position"] == "top":
                _shade(img, 0, min(HEIGHT, y0 + block_h + t["margin"]), t["shade"], fade_down=False)
            elif t["position"] == "bottom":
                _shade(img, max(0, y0 - t["margin"]), HEIGHT, t["shade"], fade_down=True)
        draw = ImageDraw.Draw(img)
        for i, line in enumerate(lines):
            w = draw.textlength(line, font=font)
            x = {"left": t["margin"], "right": WIDTH - t["margin"] - w}.get(t["align"], (WIDTH - w) / 2)
            draw.text(
                (x, y0 + i * line_h), line, font=font, fill=t["text_color"],
                stroke_width=t["stroke_width"], stroke_fill=t["stroke_color"],
            )  # fmt: skip
    logo_path = _asset(t["logo"])
    if logo_path:
        logo = Image.open(logo_path).convert("RGBA")
        h = t["logo_height"]
        logo = logo.resize((max(1, round(logo.width * h / logo.height)), h))
        m = t["margin"] // 2
        x = m if "left" in t["logo_position"] else WIDTH - m - logo.width
        y = m if "top" in t["logo_position"] else HEIGHT - m - logo.height
        img.paste(logo, (x, y), logo)
    return img


def save_jpeg(img: Image.Image, out: Path, max_bytes: int = MAX_BYTES) -> Path:
    """JPEG under `max_bytes` (YouTube's limit is 2 MB): lower the quality until it fits."""
    out.parent.mkdir(parents=True, exist_ok=True)
    for quality in (92, 88, 84, 80, 75, 70, 60, 50):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        if buf.tell() <= max_bytes:
            break
    tmp = out.with_name(out.stem + ".part.jpg")
    tmp.write_bytes(buf.getvalue())
    tmp.replace(out)
    return out


def render_to(frame: Path, text: str, out: Path, template: dict[str, Any] | None = None) -> Path:
    return save_jpeg(render(frame, text, template), out)
