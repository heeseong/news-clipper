"""Thumbnails (spec 5.7, plan 4.5): 1280x720 JPG of at most 2 MB.

Per kind, after the station's YouTube channel (2026-11-20):

- reports, style `headline`: the thumbnail text in big letters at the bottom left, first line
  yellow, second line white. The design is a template image (`overlay_image`, a 1280x720 PNG with
  transparency: logo, bands …) laid over the frame before the text; it is uploaded per program
  (PUT /api/programs/{id}/thumbnail-overlay). Until there is one, a placeholder design is drawn
  (blue corner band with the station name, blue diagonal band behind the text).
- straight briefs, style `frame`: the frame as it is. The candidate frames favour the footage with
  the on-air title caption at the bottom (candidates.caption_score), so nothing is drawn.

The program profile's `thumbnail_template` (JSON) changes the defaults: flat keys apply to both
kinds, `"report": {...}` and `"brief": {...}` override per kind. Fonts and images are files under
STORAGE_ROOT/branding/ (or absolute paths).

    {"report": {"overlay_image": "programs/1/report_overlay.png", "text_box": [48, 470, 1184, 210]}}
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.core.config import get_settings

WIDTH, HEIGHT = 1280, 720
MAX_BYTES = 2 * 1024 * 1024
FONT = Path(__file__).parent / "fonts" / "NanumGothic-ExtraBold.ttf"
KINDS = ("report", "brief")

DEFAULT_TEMPLATES: dict[str, dict[str, Any]] = {
    "report": {
        "style": "headline",
        "overlay_image": None,  # template PNG (1280x720, transparent) laid over the frame
        "text_box": None,  # [x, y, width, height] for the text, bottom-aligned; default: bottom left
        "font": None,  # default: bundled NanumGothic ExtraBold
        "font_size": 92,
        "min_font_size": 56,
        "max_lines": 2,
        "margin": 48,
        "line_spacing": 1.12,
        "line_colors": ["#FFD31A", "#FFFFFF"],
        "stroke_color": "#0B1A3A",
        "stroke_width": 5,
        "shade": 0.55,  # dark gradient over the lower part (0 with a template image unless set)
        # Placeholder design, used only without a template image:
        "accent_color": "#2D5BD7",
        "logo": None,  # PNG; default: the station name as text
        "logo_text": None,
        "logo_height": 84,
    },
    "brief": {"style": "frame"},
}
TEMPLATE_KEYS = set().union(*(t.keys() for t in DEFAULT_TEMPLATES.values()))


def branding_dir() -> Path:
    return get_settings().storage_root / "branding"


def template_for(kind: str, profile_template: dict[str, Any] | None) -> dict[str, Any]:
    kind = kind if kind in KINDS else "report"
    p = profile_template or {}
    flat = {k: v for k, v in p.items() if k in TEMPLATE_KEYS}
    own = {k: v for k, v in (p.get(kind) or {}).items() if k in TEMPLATE_KEYS}
    t = DEFAULT_TEMPLATES[kind] | flat | own
    if t.get("overlay_image") and "shade" not in flat and "shade" not in own:
        t["shade"] = 0  # the template image carries its own design
    return t


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


def _placeholder_bands(img: Image.Image, t: dict[str, Any], text_top: int | None) -> None:
    """Blue corner band (logo) and diagonal band (text) until the real template image arrives."""
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.polygon([(int(WIDTH * 0.66), 0), (WIDTH, 0), (WIDTH, int(HEIGHT * 0.36))], fill=_rgba(t["accent_color"], 0.9))
    if text_top is not None:
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


def overlay_path(t: dict[str, Any]) -> Path | None:
    return _asset(t.get("overlay_image"))


def _headline(img: Image.Image, text: str, t: dict[str, Any]) -> None:
    m = t["margin"]
    template = overlay_path(t)
    x, y, w, h = t["text_box"] or (m, 0, WIDTH - 2 * m, HEIGHT - m)
    y0 = None
    if text:
        font, lines = layout(text, t, w)
        ascent, descent = font.getmetrics()
        line_h = int((ascent + descent) * t["line_spacing"])
        y0 = y + h - line_h * len(lines) + int(descent * 0.6)  # bottom-aligned in the box
        _shade(img, max(0, y0 - 160), t["shade"])
    if template:
        layer = Image.open(template).convert("RGBA")
        if layer.size != (WIDTH, HEIGHT):
            layer = layer.resize((WIDTH, HEIGHT), Image.LANCZOS)
        img.paste(layer, (0, 0), layer)
    else:
        _placeholder_bands(img, t, y0)
        _logo(img, t)
    if text:
        colors = t["line_colors"] or ["#FFFFFF"]
        # One line: white, like the second line of a two-line headline.
        offset = len(colors) - len(lines) if len(lines) < len(colors) else 0
        for i, line in enumerate(lines):
            _text(img, (x, y0 + i * line_h), line, font, colors[min(i + offset, len(colors) - 1)], t)


def render(frame: Path, text: str, template: dict[str, Any]) -> Image.Image:
    """`template` is a full template (see template_for)."""
    img = Image.open(frame).convert("RGB")
    if img.size != (WIDTH, HEIGHT):
        img = img.resize((WIDTH, HEIGHT))
    if template["style"] == "headline":
        _headline(img, text.strip(), template)
    return img  # "frame": the picture as it is


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
