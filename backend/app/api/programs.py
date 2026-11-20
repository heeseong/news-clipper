"""Program profiles (spec 7). Minimal for the upload form; the full admin screen is M9."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.models import ProgramProfile
from app.schemas.program import ProgramIn, ProgramOut

router = APIRouter(prefix="/api/programs", tags=["programs"])


def _sources(names: list[str]) -> list[str]:
    return list(dict.fromkeys(n.strip() for n in names if n.strip()))


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
    if body.pronunciations is not None:
        program.pronunciations = {k.strip(): v.strip() for k, v in body.pronunciations.items() if k.strip()}
    db.commit()
    return program
