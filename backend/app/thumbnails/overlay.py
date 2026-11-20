"""Thumbnail text overlay (spec 5.7, plan 4.5): 1280x720 JPG of at most 2 MB.

Two styles, after the station's YouTube channel (2026-11-20):

- `headline` (reports): the logo on a blue corner band at the top right; the thumbnail text in big
  letters at the bottom left on a blue diagonal band, first line yellow, second line white.
- `lower_third` (straight briefs): the title on a white caption bar at the bottom, like an on-air
  super, and a small logo at the top right.

The design comes from the program profile's `thumbnail_template` (JSON). Flat keys apply to both
kinds; `"report": {...}` and `"brief": {...}` override per kind. Keys left out use
DEFAULT_TEMPLATES. Fonts and logos are files under STORAGE_ROOT/branding/ (or absolute paths).

    {"logo": "mokpo_mbc_news.png", "report": {"line_colors": ["#FFD31A", "#FFFFFF"]}}

Without a logo file the station name is drawn as text (`logo_text`) as a placeholder.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.core.config import get_settings
from app.metadata.defaults import split_at_separator

WIDTH, HEIGHT = 1280, 720
MAX_BYTES = 2 * 1024 * 1024
FONT = Path(__file__).parent / "fonts" / "NanumGothic-ExtraBold.ttf"
KINDS = ("report", "brief")

COMMON: dict[str, Any] = {
    "font": None,  # default: bundled NanumGothic ExtraBold
    "margin": 48,
    "line_spacing": 1.12,
    "logo": None,  # PNG with transparency
    "logo_text": None,  # placeholder when there is no logo file; default: station name + NEWS
    "logo_height": 84,
    "accent_color": "#2D5BD7",  # MBC blue: corner and diagonal bands, caption tab
}
DEFAULT_TEMPLATES: dict[str, dict[str, Any]] = {
    "report": COMMON
    | {
        "style": "headline",
        "font_size": 92,
        "min_font_size": 56,
        "max_lines": 2,
        "line_colors": ["#FFD31A", "#FFFFFF"],
        "stroke_color": "#0B1A3A",
        "stroke_width": 5,
        "shade": 0.55,  # dark gradient over the lower part
        "corner_band": True,  # blue band behind the logo
        "diagonal_band": True,  # blue band behind the text
    },
    "brief": COMMON
    | {
        "style": "lower_third",
        "font_size": 50,
        "min_font_size": 34,
        "one_line_min_size": 42,
        "max_lines": 2,
        "logo_height": 56,
        "bar_color": "#FFFFFF",
        "bar_opacity": 0.94,
        "text_color": "#10204A",
        "corner_band": False,
    },
}
TEMPLATE_KEYS = set().union(*(t.keys() for t in DEFAULT_TEMPLATES.values()))


def branding_dir() -> Path:
    return get_settings().storage_root / "branding"


def template_for(kind: str, profile_template: dict[str, Any] | None) -> dict[str, Any]:
    kind = kind if kind in KINDS else "report"
    p = profile_template or {}
    flat = {k: v for k, v in p.items() if k in TEMPLATE_KEYS}
    own = {k: v for k, v in (p.get(kind) or {}).items() if k in TEMPLATE_KEYS}
    return DEFAULT_TEMPLATES[kind] | flat | own


def unknown_keys(profile_template: dict[str, Any]) -> list[str]:
    bad = [k for k in profile_template if k not in TEMPLATE_KEYS and k not in KINDS]
    for kind in KINDS:
        sub = profile_template.get(kind)
        if sub is not None and not isinstance(sub, dict):
            bad.append(kind)
        elif sub:
            bad += [f"{kind}.{k}" for k in sub if k not in TEMPLATE_KEYS]
    return sorted(bad)


def _asset(name: str | None) -> Path | None:
    if not name:
        return None
    p = Path(name)
    p = p if p.is_absolute() else branding_dir() / p
    return p if p.exists() else None


def _font(t: dict[str, Any], size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_asset(t["font"]) or FONT), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> list[str]:
    """Keep the given line breaks; wrap longer lines at spaces."""
    lines: list[str] = []
    for para in text.splitlines():
        line = ""
        for w in para.split():
            trial = f"{line} {w}".strip()
            if draw.textlength(trial, font=font) <= max_width or not line:
                line = trial
            else:
                lines.append(line)
                line = w
        if line:
            lines.append(line)
    return lines


def layout(text: str, t: dict[str, Any], max_width: float) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Largest font size at which the text fits in `max_lines` lines (or the smallest size)."""
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    size = t["font_size"]
    while True:
        font = _font(t, size)
        lines = _wrap(draw, text, font, max_width)
        fits = len(lines) <= t["max_lines"] and all(draw.textlength(ln, font=font) <= max_width for ln in lines)
        if fits or size <= t["min_font_size"]:
            return font, lines  # may be more than max_lines at the smallest size: never drop text
        size -= 2


def _rgba(color: str, alpha: float) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), round(255 * alpha)


def _shade(img: Image.Image, top: int, strength: float) -> None:
    """Dark gradient from `top` to the bottom edge."""
    h = HEIGHT - top
    if h <= 0 or strength <= 0:
        return
    alpha = Image.linear_gradient("L").resize((WIDTH, h)).point(lambda v: int(v * strength))
    img.paste(Image.new("RGB", (WIDTH, h), "black"), (0, top), alpha)


def _bands(img: Image.Image, t: dict[str, Any], text_top: int | None) -> None:
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if t.get("corner_band"):
        d.polygon([(int(WIDTH * 0.66), 0), (WIDTH, 0), (WIDTH, int(HEIGHT * 0.36))], fill=_rgba(t["accent_color"], 0.9))
    if t.get("diagonal_band") and text_top is not None:
        top = max(0, text_top - 70)
        d.polygon([(0, top), (int(WIDTH * 0.42), top), (int(WIDTH * 0.66), HEIGHT), (0, HEIGHT)],
                  fill=_rgba(t["accent_color"], 0.55))  # fmt: skip
    img.paste(layer, (0, 0), layer)


def _logo(img: Image.Image, t: dict[str, Any]) -> None:
    h = t["logo_height"]
    m = t["margin"] // 2 + 4
    path = _asset(t["logo"])
    if path:
        logo = Image.open(path).convert("RGBA")
        logo = logo.resize((max(1, round(logo.width * h / logo.height)), h), Image.LANCZOS)
        img.paste(logo, (WIDTH - m - logo.width, m), logo)
        return
    text = t["logo_text"] or f"{get_settings().station_name}\nNEWS"
    lines = text.splitlines()
    font = _font(t, max(12, int(h / len(lines) / 1.15)))
    draw = ImageDraw.Draw(img)
    y = m
    for line in lines:
        w = draw.textlength(line, font=font)
        draw.text((WIDTH - m - w, y), line, font=font, fill="white")
        y += int(font.size * 1.1)


def _text(img: Image.Image, xy: tuple[float, float], line: str, font, fill: str, t: dict[str, Any]) -> None:
    """Text with a soft drop shadow and an outline, readable on any picture."""
    if t.get("stroke_width"):
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text((xy[0] + 4, xy[1] + 5), line, font=font, fill=(0, 0, 0, 160),
                                    stroke_width=t["stroke_width"], stroke_fill=(0, 0, 0, 160))  # fmt: skip
        shadow = shadow.filter(ImageFilter.GaussianBlur(4))
        img.paste(shadow, (0, 0), shadow)
    ImageDraw.Draw(img).text(xy, line, font=font, fill=fill,
                             stroke_width=t.get("stroke_width", 0), stroke_fill=t.get("stroke_color"))  # fmt: skip


def _headline(img: Image.Image, text: str, t: dict[str, Any]) -> None:
    m = t["margin"]
    if not text:
        _bands(img, t, None)
        return
    font, lines = layout(text, t, WIDTH - 2 * m)
    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * t["line_spacing"])
    y0 = HEIGHT - m - line_h * len(lines) + int(descent * 0.6)
    _shade(img, max(0, y0 - 160), t["shade"])
    _bands(img, t, y0)
    colors = t["line_colors"] or ["#FFFFFF"]
    # One line: white, like the second line of a two-line headline.
    offset = len(colors) - len(lines) if len(lines) < len(colors) else 0
    for i, line in enumerate(lines):
        _text(img, (m, y0 + i * line_h), line, font, colors[min(i + offset, len(colors) - 1)], t)


def _two_lines(text: str) -> str:
    parts = split_at_separator(text)
    return "\n".join(parts) if parts else text


def _lower_third(img: Image.Image, text: str, t: dict[str, Any]) -> None:
    if not text:
        return
    m = t["margin"]
    tab = 22  # accent tab at the left of the bar
    pad_x, pad_y = 28, 18
    max_w = WIDTH - 2 * m - tab - 2 * pad_x
    one_line = " ".join(text.split())
    # One line where it fits at a readable size, else two lines (split at the ellipsis).
    font, lines = layout(one_line, t | {"max_lines": 1, "min_font_size": t["one_line_min_size"]}, max_w)
    if ImageDraw.Draw(img).textlength(lines[0], font=font) > max_w or len(lines) > 1:
        font, lines = layout(_two_lines(one_line), t, max_w)
    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * t["line_spacing"])
    bar_h = line_h * len(lines) + 2 * pad_y
    top = HEIGHT - m - bar_h
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle([m, top, WIDTH - m, top + bar_h], fill=_rgba(t["bar_color"], t["bar_opacity"]))
    d.rectangle([m, top, m + tab, top + bar_h], fill=_rgba(t["accent_color"], 1.0))
    img.paste(layer, (0, 0), layer)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((m + tab + pad_x, top + pad_y + i * line_h), line, font=font, fill=t["text_color"])


def render(frame: Path, text: str, template: dict[str, Any]) -> Image.Image:
    """`template` is a full template (see template_for)."""
    img = Image.open(frame).convert("RGB")
    if img.size != (WIDTH, HEIGHT):
        img = img.resize((WIDTH, HEIGHT))
    text = text.strip()
    if template["style"] == "lower_third":
        _lower_third(img, text, template)
    else:
        _headline(img, text, template)
    _logo(img, template)
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


def render_to(frame: Path, text: str, out: Path, template: dict[str, Any]) -> Path:
    return save_jpeg(render(frame, text, template), out)
