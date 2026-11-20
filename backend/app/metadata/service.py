"""The metadata step (pipeline 7) and regeneration (plan 4.6).

Defaults come from the script. When an LLM is configured its suggestions are added as candidates
after a fact check (spec 5.7); an LLM failure only leaves a warning (spec 10), so a job never
fails because of the LLM.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Clip, ClipMetadata, Job
from app.models.base import utcnow

from . import defaults, factcheck
from .llm import THUMBNAIL_TEXT_MAX, LLMError, LLMProvider, thumbnail_length
from .script import ClipScript, clip_script

log = logging.getLogger(__name__)


@dataclass
class Generated:
    title: str
    title_candidates: list[dict]
    description: str
    summary: str | None
    tags: list[str]
    hashtags: list[str]
    thumbnail_text: str
    thumbnail_text_candidates: list[dict]
    warnings: list[str]
    llm_status: str = "off"
    llm_provider: str | None = None
    llm_model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


def _candidate(text: str, source: str, warnings: list[str] | None = None) -> dict:
    return {"text": text, "source": source, "warnings": warnings or []}


def generate(
    script: ClipScript,
    provider: LLMProvider | None,
    footer_template: str | None = None,
    default_tags: list[str] | None = None,
) -> Generated:
    """Pure part: no database. Script-based defaults plus checked LLM candidates."""
    base = defaults.build(script, footer_template, default_tags)
    out = Generated(
        title=base.title,
        title_candidates=[_candidate(base.title, "script")],
        description=base.description,
        summary=None,
        tags=base.tags,
        hashtags=[],
        thumbnail_text=base.thumbnail_text,
        thumbnail_text_candidates=[_candidate(base.thumbnail_text, "title")] if base.thumbnail_text else [],
        warnings=[],
    )
    if len(base.title) > 100:
        out.warnings.append("제목이 유튜브 제한(100자)보다 깁니다")
    if provider is None:
        return out
    out.llm_provider, out.llm_model = provider.name, provider.model
    try:
        result = provider.suggest(script)
    except LLMError as e:
        out.llm_status = "failed"
        out.input_tokens, out.output_tokens = e.input_tokens, e.output_tokens
        out.warnings.append(f"LLM 보완 실패 (원고 기반 기본값만 사용): {e}")
        return out
    except Exception as e:  # a provider bug must not fail the step either
        log.exception("LLM provider error")
        out.llm_status = "failed"
        out.warnings.append(f"LLM 보완 실패 (원고 기반 기본값만 사용): {type(e).__name__}")
        return out

    s = result.suggestions
    ref = script.all_text
    out.llm_status, out.llm_model = "ok", result.model
    out.input_tokens, out.output_tokens = result.input_tokens, result.output_tokens

    for i, title in enumerate(s.titles, 1):
        if title != base.title:
            out.title_candidates.append(_candidate(title, "llm", factcheck.warnings_for(f"대안 제목 {i}", title, ref)))
    if script.kind == "report" and s.summary:
        out.summary = s.summary
        out.warnings += factcheck.warnings_for("요약", s.summary, ref)

    # Keywords and hashtags are not shown to the reviewer as prominently: drop the doubtful ones.
    dropped: list[str] = []
    keywords = []
    for k in s.keywords:
        (dropped if factcheck.missing_from_script(k, ref) else keywords).append(k)
    hashtags = []
    for h in s.hashtags:
        (dropped if factcheck.missing_from_script(h, ref) else hashtags).append(h)
    if dropped:
        out.warnings.append(f"원고에서 확인되지 않아 뺀 키워드·해시태그: {', '.join(dropped)}")
    out.tags = defaults.tags(script, default_tags, keywords)
    out.hashtags = hashtags

    out.warnings += [w for c in out.title_candidates for w in c["warnings"]]
    if script.kind == "brief":  # no text on a brief's thumbnail
        return out
    thumb_warnings = factcheck.warnings_for("썸네일 문구", s.thumbnail_text, ref)
    llm_thumb = _candidate(s.thumbnail_text, "llm", thumb_warnings)
    # The script memo is always empty (spec 5.7). The channel's report headline is the title split
    # at its ellipsis. Only a title without an ellipsis is replaced by the LLM's two lines, when
    # they pass the check.
    use_llm = (
        defaults.split_at_separator(script.title) is None
        and not thumb_warnings
        and thumbnail_length(s.thumbnail_text) <= THUMBNAIL_TEXT_MAX
    )
    if use_llm:
        out.thumbnail_text = s.thumbnail_text
        out.thumbnail_text_candidates.insert(0, llm_thumb)
    else:
        out.thumbnail_text_candidates.append(llm_thumb)
    out.warnings += thumb_warnings
    return out


def store(session: Session, clip: Clip, g: Generated) -> ClipMetadata:
    """Create or overwrite the clip's metadata. Publishing settings are kept."""
    meta = session.get(ClipMetadata, clip.id) or ClipMetadata(clip_id=clip.id)
    meta.title = g.title
    meta.title_candidates = g.title_candidates
    meta.description = g.description
    meta.summary = g.summary
    meta.tags = g.tags
    meta.hashtags = g.hashtags
    meta.thumbnail_text = g.thumbnail_text
    meta.thumbnail_text_candidates = g.thumbnail_text_candidates
    meta.warnings = g.warnings
    meta.llm_status = g.llm_status
    meta.llm_provider = g.llm_provider
    meta.llm_model = g.llm_model
    meta.llm_input_tokens = g.input_tokens
    meta.llm_output_tokens = g.output_tokens
    meta.generated_at = utcnow()
    session.add(meta)
    session.flush()
    return meta


@dataclass
class ClipInput:
    clip_id: int
    script: ClipScript
    footer_template: str | None
    default_tags: list[str] | None


def clip_input(session: Session, clip: Clip) -> ClipInput:
    job = session.get(Job, clip.job_id)
    program = job.program
    return ClipInput(
        clip.id,
        clip_script(clip, job, get_settings().station_name),
        program.description_footer if program else None,
        list(program.default_tags or []) if program else None,
    )


def run(inp: ClipInput, provider: LLMProvider | None) -> Generated:
    g = generate(inp.script, provider, inp.footer_template, inp.default_tags)
    log.info(
        "metadata generated",
        extra={"clip_id": inp.clip_id, "llm": g.llm_status, "input_tokens": g.input_tokens,
               "output_tokens": g.output_tokens, "warnings": len(g.warnings)},
    )
    return g


def generate_for_clip(session: Session, clip: Clip, provider: LLMProvider | None) -> ClipMetadata:
    return store(session, clip, run(clip_input(session, clip), provider))


def generate_for_clips(
    clip_ids: list[int],
    provider: LLMProvider | None,
    *,
    threads: int = 4,
    on_stored: Callable[[int], None] | None = None,
) -> None:
    """LLM calls run in parallel (they wait on the network); each clip is committed when done."""
    with session_scope() as session:
        inputs = [clip_input(session, session.get(Clip, i)) for i in clip_ids]
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        futures = {pool.submit(run, inp, provider): inp.clip_id for inp in inputs}
        for future in as_completed(futures):
            clip_id = futures[future]
            g = future.result()
            with session_scope() as session:
                clip = session.get(Clip, clip_id)
                if clip is None:  # removed meanwhile (align re-run)
                    continue
                store(session, clip, g)
            if on_stored:
                on_stored(clip_id)


def clips_to_process(session: Session, job_id: int) -> list[int]:
    """Clips with a range. Clips without one get metadata once the reviewer sets it (M5, 4.6)."""
    return list(
        session.scalars(
            select(Clip.id).where(Clip.job_id == job_id, Clip.start_sec.is_not(None), Clip.end_sec.is_not(None))
        )
    )
