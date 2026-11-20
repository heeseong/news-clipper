"""Alignment and clip boundaries (M3) on synthetic log-probabilities with known speech times.

The real acoustic model is never loaded; `speech_logprobs` builds what the model "heard".
"""

from __future__ import annotations

import json
import wave
from dataclasses import dataclass
from datetime import date

import numpy as np
import pytest

from app.align import aligner, boundaries
from app.align.aligner import AlignItem, AlignStatus, align_rundown
from app.align.logprobs import LogProbs
from app.align.service import plan_clips
from app.core.db import session_scope
from app.models import AlignedSentence, Clip, ClipStatus, Job, JobStatus, ProgramProfile, Rundown, StepStatus
from app.newsroom import factory
from app.newsroom import rundown as rd
from app.newsroom import service as newsroom
from app.newsroom.fake import FakeNewsroomProvider
from app.newsroom.models import ItemType, Section, Sentence, SentenceKind
from app.pipeline import orchestrator as orch
from app.pipeline import steps as step_registry
from app.pipeline import tasks
from app.storage import JobFiles
from tests.fixtures.logprobs import FILLER, FRAME, speech_logprobs, spoken_seconds, vocab_for

FPS = 25.0
PAUSE = 0.6  # silence between sentences
# ctc-segmentation places a character's time up to one synthetic syllable (0.2 s) after its peak.
ONSET_TOL = 0.3


def S(text: str, section=Section.REPORT, kind=SentenceKind.NARRATION) -> Sentence:
    return Sentence(section=section, kind=kind, text=text)


def report(name: str, lead: str, body: str) -> list[Sentence]:
    return [
        S(lead, Section.LEAD, SentenceKind.ANCHOR),
        S(f"{name} 기자입니다.", Section.LEAD, SentenceKind.INTRO),
        S(body),
        S(f"엠비씨 뉴스 {name}입니다.", kind=SentenceKind.SIGNOFF),
    ]


R1 = report("홍길동", "새 다리가 오늘 개통했습니다.", "길이 일 킬로미터의 다리가 문을 열었습니다.")
B2 = [
    S("군이 겨울 축제 준비에 들어갔습니다.", Section.LEAD, SentenceKind.ANCHOR),
    S("올해는 체험 행사가 늘어나고 먹거리 장터도 새로 열립니다.", Section.LEAD, SentenceKind.ANCHOR),
]
R3 = report("김영희", "섬마을 의료 공백을 짚어봅니다.", "배로 한 시간을 가야 병원이 나옵니다.")
R4 = report("최지훈", "항구 정비 사업이 시작됐습니다.", "낡은 방파제를 새로 쌓는 공사입니다.")


@dataclass
class Broadcast:
    lp: LogProbs
    items: list[AlignItem]
    cuts: list[float]
    silences: list[tuple[float, float]]
    starts: dict[int, float]  # true onset of each item's first sentence
    ends: dict[int, float]  # true offset of each item's last sentence


def broadcast(
    blocks: list[tuple[int, list[Sentence] | None, bool]],
    *,
    preamble: float = 20.0,
    gap: float = 1.5,
    greeting_before: int | None = None,
    duration: float | None = None,
    planned_scale: float = 1.0,
) -> Broadcast:
    """Lay the items out in time: unrelated speech (another newscast) first, then each item with
    an anchor cut 0.6 s before it and PAUSE seconds of silence between sentences."""
    speech: list[tuple[float, str]] = []
    texts = []
    filler = (FILLER + " ") * 3
    speech.append((1.0, filler))
    t = preamble
    cuts, silences, starts, ends, items = [], [], {}, {}, []
    silence_from = 1.0 + spoken_seconds(filler)
    for order, sents, upload in blocks:
        if sents is None:  # script-less item (title, bridge): 10 s of music, one cut
            cuts.append(t)
            t += 10.0
            items.append(AlignItem(order, ItemType.OTHER, False, 10.0, None, []))
            continue
        cuts.append(round(t - 0.6, 3))
        if greeting_before == order:  # greeting over the anchor shot, then a pause, then the lead
            speech.append((t - 0.2, "안녕하십니까 뉴스입니다"))
            t += -0.2 + spoken_seconds("안녕하십니까 뉴스입니다") + 0.2
            silences.append((silence_from, round(t - 0.8 - 0.4, 3)))
            silence_from = t
            t += 0.8
        start = t
        for s in sents:
            silences.append((round(silence_from, 3), round(t, 3)))
            speech.append((t, s.text))
            texts.append(s.text)
            t += spoken_seconds(s.text)
            silence_from = t
            t += PAUSE
        starts[order], ends[order] = start, silence_from
        # Cue sheet length: a little longer than the speech (the clip adds the pauses around it).
        planned = (silence_from - start) * 1.1 * planned_scale
        kind = ItemType.REPORT if any(s.section == Section.REPORT for s in sents) else ItemType.BRIEF
        items.append(AlignItem(order, kind, upload, round(planned, 1), None, sents))
        t += gap
    total = duration or t + 5.0
    silences.append((round(silence_from, 3), total))
    lp = speech_logprobs(total, speech, vocab_for(filler, "안녕하십니까 뉴스입니다", *texts))
    return Broadcast(lp, items, cuts, silences, starts, ends)


def test_items_are_found_in_order():
    b = broadcast([(1, None, False), (2, R1, True), (3, B2, False), (4, R3, True)])
    res = align_rundown(b.lp, b.items, b.cuts)
    assert [r.status for r in res] == [AlignStatus.NO_SCRIPT, *[AlignStatus.ALIGNED] * 3]
    for r in res[1:]:
        # Like the real model, a first character can be placed early, in the silence before it;
        # the boundary rules move such onsets to the silence end (refine_onset).
        onset = boundaries.refine_onset(r.onset, b.silences)[0]
        assert onset == pytest.approx(b.starts[r.order], abs=ONSET_TOL)
        assert r.offset == pytest.approx(b.ends[r.order], abs=ONSET_TOL)
        assert r.flags == [] and r.median_score > -1
    # Sentences carry first/last character times, not padded bounds.
    first = res[1].sentences[0]
    assert first.sentence.kind == SentenceKind.ANCHOR


def test_report_boundaries_snap_to_anchor_cuts():
    b = broadcast([(1, R1, True), (2, B2, False), (3, R3, True), (4, R4, True)])
    result = plan_clips(b.lp, b.items, b.cuts, b.silences, FPS)
    clips = {c.order: c for c in result.clips}
    assert set(clips) == {1, 3, 4}
    for order, nxt in ((1, 2), (3, 4)):
        c = clips[order]
        assert c.status == ClipStatus.DRAFT and c.flags == []
        cut = b.cuts[order - 1]
        assert c.start == pytest.approx(cut + 1 / FPS, abs=1e-3) and c.start_snap == "scene"
        assert c.end == pytest.approx(b.cuts[nxt - 1] - 0.5 / FPS, abs=1e-3) and c.end_snap == "next_item_cut"
    # The last report has no next item: sign-off end + 0.4 s.
    last = clips[4]
    assert last.end_snap == "align" and last.end == pytest.approx(b.ends[4] + 0.4, abs=0.3)
    assert result.warnings == []


def test_greeting_before_the_lead_is_left_out():
    b = broadcast([(1, R1, True), (2, R3, True)], greeting_before=1)
    clip = next(c for c in plan_clips(b.lp, b.items, b.cuts, b.silences, FPS).clips if c.order == 1)
    # The cut opens the anchor shot but the greeting follows it: start in the silence before the lead.
    assert clip.start_snap == "silence"
    assert b.starts[1] - 0.8 <= clip.start <= b.starts[1]
    assert clip.start > b.cuts[0] + 1.0


def test_onset_in_silence_moves_to_silence_end():
    assert boundaries.refine_onset(10.2, [(9.0, 11.0)]) == (11.0, True)
    assert boundaries.refine_onset(11.0, [(9.0, 11.0)]) == (11.0, False)
    assert boundaries.refine_onset(12.0, [(9.0, 11.0)]) == (12.0, False)


def test_recording_ends_before_the_last_item():
    long_r4 = report("최지훈", "항구 정비 사업이 시작됐습니다.", "낡은 방파제를 새로 쌓는 공사입니다. " * 15)
    b = broadcast([(1, R1, True), (2, R3, True), (3, long_r4, True)])
    cut_at = b.starts[3] + 0.5  # the recording stops right after item 3 begins: less audio than text
    rows = int(cut_at / FRAME)
    lp = LogProbs(b.lp.lpz[:rows], b.lp.frame_sec, 0.0, b.lp.vocab, b.lp.blank)
    result = plan_clips(lp, b.items, b.cuts, [s for s in b.silences if s[0] < cut_at], FPS)
    clips = {c.order: c for c in result.clips}
    assert clips[3].status == ClipStatus.NOT_FOUND and clips[3].start is None
    assert clips[1].start is not None and clips[2].start is not None
    assert any("찾지 못한 아이템: 3번" in w for w in result.warnings)


def test_wrong_scripts_are_rejected_and_flag_the_broadcast():
    b = broadcast([(1, R1, True), (2, R3, True)])
    other_day = [
        AlignItem(1, ItemType.REPORT, True, b.items[0].planned_seconds, None,
                  report("박민수", "도서관 개관 시간이 늘어납니다.", "밤 열 시까지 문을 엽니다.")),
        AlignItem(2, ItemType.REPORT, True, b.items[1].planned_seconds, None,
                  report("이정호", "짙은 안개가 꼈습니다.", "차량들이 천천히 움직입니다.")),
    ]
    vocab = b.lp.vocab + [ch for ch in "도서관개시늘어납밤열까지엽짙은안꼈량들천히움직이정호박민수" if ch not in b.lp.vocab]
    pad = np.full((b.lp.lpz.shape[0], len(vocab) - len(b.lp.vocab)), -12.0, dtype=np.float32)
    lp = LogProbs(np.hstack([b.lp.lpz, pad]), b.lp.frame_sec, 0.0, vocab, b.lp.blank)
    result = plan_clips(lp, other_day, b.cuts, b.silences, FPS)
    for c in result.clips:
        assert c.start is None and c.end is None  # no automatic range
        assert "LOW_CONFIDENCE" in c.flags or "DURATION_MISMATCH" in c.flags
        assert c.suggested_start is not None
    assert any("맞지 않을 수 있습니다" in w for w in result.warnings)


def test_duration_mismatch_rejects_the_range():
    b = broadcast([(1, R1, True), (2, R3, True)], planned_scale=2.0)  # cue sheet says twice as long
    clips = plan_clips(b.lp, b.items, b.cuts, b.silences, FPS).clips
    assert all(c.start is None and "DURATION_MISMATCH" in c.flags for c in clips)


def test_retry_from_later_cut_when_the_first_window_is_implausible(monkeypatch):
    """The first attempt is implausible (as when the text is crammed into the headlines);
    the window is moved to each later cut until a plausible alignment is found."""
    b = broadcast([(1, R1, True), (2, R3, True)])
    real = aligner.align_texts
    calls = []

    def first_attempt_crammed(lp, t0, t1, texts, full_search):
        calls.append(t0)
        out = real(lp, t0, t1, texts, full_search)
        if len(calls) == 1:  # squeeze everything into the first 3 s with bad scores
            return [(t0 + i * 0.1, t0 + i * 0.1 + 0.05, -9.0) for i in range(len(out))]
        return out

    monkeypatch.setattr(aligner, "align_texts", first_attempt_crammed)
    res = align_rundown(b.lp, b.items, b.cuts)
    assert res[0].retried_from_cut is not None and res[0].flags == []
    assert boundaries.refine_onset(res[0].onset, b.silences)[0] == pytest.approx(b.starts[1], abs=ONSET_TOL)
    assert calls[1] == pytest.approx(res[0].retried_from_cut - aligner.RETRY_BEFORE_CUT)


def test_checks():
    assert not aligner.low_confidence([-2.0, -3.0, -2.5])
    assert aligner.low_confidence([-6.0, -7.0, -2.0])  # median below -5
    assert aligner.low_confidence([-1.0, -1.0, -6.0])  # 33 % below -5
    assert not aligner.low_confidence([-1.0, -1.0, -1.0, -6.0])  # 25 %
    assert aligner.duration_off(70, 100) and not aligner.duration_off(85, 100)
    assert not aligner.duration_off(30, None)


def test_pronunciation_dictionary_reaches_the_tokens():
    vocab = set("기아케이에")
    assert aligner.to_tokens("KIA", vocab) == "기아"  # built in
    assert aligner.to_tokens("ABC", vocab, {"ABC": "기아"}) == "기아"


# --- the align step (DB, files) ------------------------------------------------------------------



def api_item(order: int, title: str, sents: list[Sentence] | None, *, report_item: bool, planned: float) -> dict:
    """An IF_MARSL_003 element whose script parses back into `sents`."""
    if sents is None:
        content = ""
    elif report_item:
        lead = [s.text for s in sents if s.section == Section.LEAD]
        body = [s.text for s in sents if s.section == Section.REPORT]
        content = "◀ 앵 커 ▶\n" + "\n".join(lead) + "\n◀ END ▶\n◀ 리포트 ▶\n" + "\n\n".join(body) + "\n◀ END ▶\n"
    else:
        content = " ".join(s.text for s in sents)
    return {
        "cueSheetId": 7001, "cueSheetItemId": 700 + order, "cueSheetItemTitle": title,
        "cueSheetItemContent": None, "cueSheetItemOrderNumber": str(order),
        "article": {
            "articleId": 7700 + order, "articleTitle": title, "articleContent": content,
            "ancMentCttTime": 0, "artclCttTime": round(planned), "artclExtTime": 0,
            "articleTypeCode": "typ_002" if report_item else "typ_001",
            "articleTypeCodeName": "리포트" if report_item else "스트레이트",
            "reporterName": "데스크", "originReporterName": "데스크", "channelName": "목포",
            "originChannelName": "목포", "isSameChannel": True, "memo": None,
        },
        "remark": "리포트" if report_item else None,
    }


@pytest.fixture
def aligned_job(app_env, monkeypatch):
    """A job whose analysis outputs and log-probs come from a synthetic broadcast."""
    # Spoken numbers are written as Hangul here so the parsed script matches the synthetic speech.
    b = broadcast([(1, R1, True), (2, B2, False), (3, R3, True)])
    raw = [
        api_item(1, "새 다리 개통(R)", R1, report_item=True, planned=b.items[0].planned_seconds),
        api_item(2, "축제 준비", B2, report_item=False, planned=b.items[1].planned_seconds),
        api_item(3, "섬마을 의료", R3, report_item=True, planned=b.items[2].planned_seconds),
    ]
    cue = {"cueSheetId": 7001, "programName": "뉴스데스크", "programType": "TV", "broadcastDate": "2026-11-04",
           "broadcastStartTime": "19:30:00", "cueSheetVersion": 2}
    provider = FakeNewsroomProvider([cue], {7001: raw})
    factory.set_provider(provider)
    for step in ("proxy", "analyze"):
        monkeypatch.setitem(step_registry._HANDLERS, step, lambda ctx: None)
    with session_scope() as s:
        prog = ProgramProfile(name="뉴스데스크")
        job = Job(status=JobStatus.UPLOADED, broadcast_date=date(2026, 11, 4), program=prog, fps=FPS)
        orch.create_steps(job)
        s.add(job)
        s.flush()
        job_id = job.id
    files = JobFiles(job_id)
    files.scenes.parent.mkdir(parents=True, exist_ok=True)
    files.scenes.write_text(json.dumps({"cuts": [{"time": c, "frame": 0, "score": 0.5} for c in b.cuts]}))
    files.silences.write_text(json.dumps({"silences": [{"start": a, "end": e} for a, e in b.silences]}))
    files.align_audio.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(files.align_audio), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 1600)
    b.lp.save(files.logprobs)  # cached log-probs: the model is not needed
    yield job_id, b, provider
    factory.set_provider(None)


def test_align_step_creates_clips(aligned_job):
    job_id, b, _ = aligned_job
    tasks.start_job(job_id)
    with session_scope() as s:
        job = s.get(Job, job_id)
        assert {r.step: r.status for r in job.steps}["align"] == StepStatus.SUCCEEDED
        assert job.status == JobStatus.REVIEW_READY
        rundown = s.query(Rundown).filter_by(job_id=job_id).one()
        items = {i.item_no: i for i in rundown.items}
        assert [items[n].align_status for n in (1, 2, 3)] == ["ALIGNED"] * 3
        assert items[1].aligned_start == pytest.approx(b.starts[1], abs=ONSET_TOL)
        clips = {c.rundown_item.item_no: c for c in s.query(Clip).filter_by(job_id=job_id)}
        assert set(clips) == {1, 2, 3}  # reports and the straight brief
        brief = clips[2]
        assert brief.start_sec == pytest.approx(b.cuts[1] + 1 / FPS, abs=1e-3)
        assert brief.end_sec == pytest.approx(b.cuts[2] - 0.5 / FPS, abs=1e-3)
        assert brief.flags == []  # no sign-off is expected in a brief
        assert clips[1].start_sec == pytest.approx(b.cuts[0] + 1 / FPS, abs=1e-3)
        assert clips[1].end_sec == pytest.approx(b.cuts[1] - 0.5 / FPS, abs=1e-3)
        assert clips[1].flags == [] and clips[1].status == "DRAFT" and not clips[1].needs_manual_range
        sents = s.query(AlignedSentence).filter_by(rundown_item_id=items[1].id).order_by(AlignedSentence.seq).all()
        assert [x.kind for x in sents] == ["anchor", "intro", "narration", "signoff"]
    out = json.loads(JobFiles(job_id).alignment.read_text())
    assert [c["order"] for c in out["clips"]] == [1, 2, 3]

    # Running the step again replaces the clips instead of adding more.
    tasks.retry_step(job_id, "align")
    with session_scope() as s:
        assert s.query(Clip).filter_by(job_id=job_id).count() == 3


def test_align_step_uses_the_latest_edition(aligned_job):
    job_id, b, provider = aligned_job
    tasks.start_job(job_id)
    # The desk edits the cue sheet after the newsroom step: item 2 (brief) is dropped, edition 3.
    provider.items[7001] = [provider.items[7001][0], {**provider.items[7001][2], "cueSheetItemOrderNumber": "2"}]
    provider.cue_sheets[0]["cueSheetVersion"] = 3
    tasks.retry_step(job_id, "align")
    with session_scope() as s:
        rundown = s.query(Rundown).filter_by(job_id=job_id).one()
        assert rundown.edition == 3 and len(rundown.items) == 2
        assert rundown.changes[-1]["removed"] == [{"item_no": 2, "title": "축제 준비"}]
        clips = {c.rundown_item.item_no for c in s.query(Clip).filter_by(job_id=job_id)}
        assert clips == {1, 2}  # the two reports left (the brief was removed)


def test_align_step_without_model_fails_clearly(aligned_job, monkeypatch):
    from app.align import acoustic

    job_id, _, _ = aligned_job
    JobFiles(job_id).logprobs.unlink()

    def missing(*a, **k):
        raise acoustic.ModelUnavailable("정렬 모델 패키지(torch, transformers)가 설치돼 있지 않습니다")

    monkeypatch.setattr(acoustic, "compute_logprobs", missing)
    tasks.start_job(job_id)
    with session_scope() as s:
        job = s.get(Job, job_id)
        assert job.status == JobStatus.FAILED and "torch" in job.error


def test_newsroom_refetch_drops_clips_of_old_items(aligned_job):
    job_id, _, provider = aligned_job
    tasks.start_job(job_id)
    with session_scope() as s:
        job = s.get(Job, job_id)
        fetched = newsroom.fetch(provider, job.broadcast_date, newsroom.ProgramInfo.of(job.program))
        newsroom.store(s, job, fetched)
    with session_scope() as s:
        assert s.query(Clip).filter_by(job_id=job_id).count() == 0
        assert s.query(AlignedSentence).count() == 0


def test_parsed_fixture_scripts_match_the_synthetic_speech():
    item = rd.build_item(api_item(1, "새 다리 개통(R)", R1, report_item=True, planned=10))
    assert [s.text for s in item.script.sentences] == [s.text for s in R1]


def test_clip_api(aligned_job, client):
    job_id, b, _ = aligned_job
    tasks.start_job(job_id)
    clips = client.get(f"/api/jobs/{job_id}/clips").json()
    assert [(c["item_no"], c["title"], c["needs_manual_range"]) for c in clips] == [
        (1, "새 다리 개통", False),
        (2, "축제 준비", False),
        (3, "섬마을 의료", False),
    ]
    sents = client.get(f"/api/clips/{clips[0]['id']}/sentences").json()
    assert [s["kind"] for s in sents] == ["anchor", "intro", "narration", "signoff"]
    item = client.get(f"/api/jobs/{job_id}/rundown").json()["items"][0]
    assert item["align_status"] == "ALIGNED" and item["aligned_start"] is not None
    assert client.get("/api/clips/999").status_code == 404

    pid = client.get("/api/programs").json()[0]["id"]
    r = client.put(f"/api/programs/{pid}", json={"name": "뉴스데스크", "pronunciations": {" LG ": "엘지"}})
    assert r.json()["pronunciations"] == {"LG": "엘지"}


def test_briefs_are_clipped_without_signoff_flag():
    b = broadcast([(1, R1, True), (2, B2, True), (3, R3, True)])
    report_without_signoff = AlignItem(3, ItemType.REPORT, True, b.items[2].planned_seconds, None, R3[:-1])
    items = [b.items[0], b.items[1], report_without_signoff]
    clips = {c.order: c for c in plan_clips(b.lp, items, b.cuts, b.silences, FPS).clips}
    assert clips[2].flags == [] and clips[2].start_snap == "scene" and clips[2].end_snap == "next_item_cut"
    assert "NO_SIGNOFF" in clips[3].flags  # a report is expected to sign off


def test_brief_end_reaches_a_later_cut_over_silent_footage():
    b = broadcast([(1, R1, True), (2, B2, True), (3, R3, True)], gap=6.0)  # 6 s of silent footage
    items = b.items
    # A report in the same place keeps the ±3 s rule: it ends after its sign-off.
    report2 = AlignItem(2, ItemType.REPORT, True, items[1].planned_seconds, None, B2)
    for second, expect in ((items[1], "next_item_cut"), (report2, "align")):
        clips = {c.order: c for c in plan_clips(b.lp, [items[0], second, items[2]], b.cuts, b.silences, FPS).clips}
        assert clips[2].end_snap == expect
        if expect == "next_item_cut":
            assert clips[2].end == pytest.approx(b.cuts[2] - 0.5 / FPS, abs=1e-3)


def test_brief_end_stays_when_something_is_said_before_the_cut():
    b = broadcast([(1, R1, True), (2, B2, True), (3, R3, True)], gap=6.0)
    # Speech (not in any script) in the gap after the brief.
    extra = speech_logprobs(b.lp.duration, [(b.ends[2] + 2.0, FILLER)], b.lp.vocab)
    spoken = extra.lpz.argmax(axis=1) != extra.blank
    lpz = b.lp.lpz.copy()
    lpz[spoken] = extra.lpz[spoken]
    lp = LogProbs(lpz, b.lp.frame_sec, 0.0, b.lp.vocab, b.lp.blank)
    assert not boundaries.silent_tail(b.ends[2], b.cuts[2], b.starts[3], lp)
    assert boundaries.silent_tail(b.ends[2], b.cuts[2], b.starts[3], b.lp)
