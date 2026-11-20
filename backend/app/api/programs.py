"""Program profiles (spec 7). Minimal for the upload form; the full admin screen is M9."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from app.api.deps import DbSession
from app.models import ProgramProfile
from app.models.program import DEFAULT_DEDUPE_PROGRAM
from app.schemas.program import ProgramIn, ProgramOut
from app.thumbnails import overlay
from app.thumbnails.overlay import unknown_keys

router = APIRouter(prefix="/api/programs", tags=["programs"])


def _sources(names: list[str]) -> list[str]:
    return list(dict.fromkeys(n.strip() for n in names if n.strip()))


def _apply_metadata_settings(program: ProgramProfile, body: ProgramIn) -> None:
    if body.description_footer is not None:
        program.description_footer = body.description_footer.strip() or None
    if body.default_tags is not None:
        program.default_tags = _sources(body.default_tags)
    if body.thumbnail_template is not None:
        unknown = unknown_keys(body.thumbnail_template)
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"알 수 없는 썸네일 템플릿 항목: {', '.join(unknown)}"
            )
        program.thumbnail_template = dict(body.thumbnail_template)


@router.get("", response_model=list[ProgramOut])
def list_programs(db: DbSession) -> list[ProgramProfile]:
    return list(db.scalars(select(ProgramProfile).order_by(ProgramProfile.name)))


@router.post("", response_model=ProgramOut, status_code=status.HTTP_201_CREATED)
def create_program(body: ProgramIn, db: DbSession) -> ProgramProfile:
    name = body.name.strip()
    if db.scalar(select(ProgramProfile).where(ProgramProfile.name == name)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"이미 있는 프로그램입니다: {name}")
    program = ProgramProfile(name=name, broadcast_time=body.broadcast_time, audio_track=body.audio_track)
    if body.excluded_sources is not None:
        program.excluded_sources = _sources(body.excluded_sources)
    if body.dedupe_program is not None:
        program.dedupe_program = body.dedupe_program.strip() or None
    else:
        program.dedupe_program = DEFAULT_DEDUPE_PROGRAM.get(name)
    _apply_metadata_settings(program, body)
    db.add(program)
    db.commit()
    return program


@router.put("/{program_id}", response_model=ProgramOut)
def update_program(program_id: int, body: ProgramIn, db: DbSession) -> ProgramProfile:
    program = db.get(ProgramProfile, program_id)
    if program is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "프로그램을 찾을 수 없습니다")
    program.name = body.name.strip()
    program.broadcast_time = body.broadcast_time
    program.audio_track = body.audio_track
    if body.excluded_sources is not None:
        program.excluded_sources = _sources(body.excluded_sources)
    if body.dedupe_program is not None:
        program.dedupe_program = body.dedupe_program.strip() or None
    if body.pronunciations is not None:
        program.pronunciations = {k.strip(): v.strip() for k, v in body.pronunciations.items() if k.strip()}
    _apply_metadata_settings(program, body)
    db.commit()
    return program


# --- Report thumbnail template image (plan M4, 2026-11-20) ---

OVERLAY_MAX_BYTES = 20 * 1024 * 1024


def overlay_rel_path(program_id: int) -> str:
    return f"programs/{program_id}/report_overlay.png"


def _program(db, program_id: int) -> ProgramProfile:
    program = db.get(ProgramProfile, program_id)
    if program is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "프로그램을 찾을 수 없습니다")
    return program


def _set_report_key(program: ProgramProfile, key: str, value) -> None:
    template = dict(program.thumbnail_template or {})
    report = dict(template.get("report") or {})
    if value is None:
        report.pop(key, None)
    else:
        report[key] = value
    template["report"] = report
    program.thumbnail_template = template  # a new dict so the JSON column is saved


@router.put("/{program_id}/thumbnail-overlay", response_model=ProgramOut)
async def upload_thumbnail_overlay(program_id: int, request: Request, db: DbSession) -> ProgramProfile:
    """The report thumbnail template: a 16:9 PNG with transparency (logo, bands …), sent as the
    raw request body. It is laid over the frame before the text. Replaces the previous one."""
    program = _program(db, program_id)
    body = await request.body()
    if not body:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "이미지 파일이 비어 있습니다")
    if len(body) > OVERLAY_MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "템플릿 이미지는 20MB 이하여야 합니다")
    try:
        img = Image.open(io.BytesIO(body))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "PNG 이미지를 읽을 수 없습니다") from e
    if img.format != "PNG":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "투명 배경 PNG 파일이어야 합니다")
    if abs(img.width / img.height - 16 / 9) > 0.02:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"16:9 이미지여야 합니다 (받은 크기 {img.width}x{img.height})"
        )
    img = img.convert("RGBA")
    if img.getextrema()[3][0] == 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "투명한 부분이 없습니다. 배경이 투명한 PNG여야 합니다")
    if img.size != (overlay.WIDTH, overlay.HEIGHT):
        img = img.resize((overlay.WIDTH, overlay.HEIGHT), Image.LANCZOS)
    rel = overlay_rel_path(program_id)
    path = overlay.branding_dir() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    img.save(tmp, "PNG")
    tmp.replace(path)
    _set_report_key(program, "overlay_image", rel)
    db.commit()
    return program


@router.delete("/{program_id}/thumbnail-overlay", response_model=ProgramOut)
def delete_thumbnail_overlay(program_id: int, db: DbSession) -> ProgramProfile:
    program = _program(db, program_id)
    (overlay.branding_dir() / overlay_rel_path(program_id)).unlink(missing_ok=True)
    _set_report_key(program, "overlay_image", None)
    db.commit()
    return program


@router.get("/{program_id}/thumbnail-preview")
def thumbnail_preview(
    program_id: int, db: DbSession, text: str = "\"공정성 외친 77일\"...\n광장 떠나 장기전으로"
) -> Response:
    """The report thumbnail design on a sample picture, to check a template."""
    program = _program(db, program_id)
    template = overlay.template_for("report", program.thumbnail_template)
    sample = Image.linear_gradient("L").resize((overlay.WIDTH, overlay.HEIGHT)).convert("RGB")
    buf = io.BytesIO()
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        sample.save(f.name)
        overlay.render(Path(f.name), text.replace("\\n", "\n"), template).save(buf, "JPEG", quality=90)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
