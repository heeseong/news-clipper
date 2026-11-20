"""tus upload → job creation → proxy/analysis pipeline (plan 1.1, 1.3, DoD)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.intake import date_in_filename
from tests.fixtures.synthetic import ffmpeg_available

TUS = {"Tus-Resumable": "1.0.0"}
needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def meta(**kv: str) -> str:
    return ",".join(f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in kv.items())


def create_upload(client, length: int, filename: str = "news.mp4"):
    return client.post(
        "/api/uploads",
        headers={**TUS, "Upload-Length": str(length), "Upload-Metadata": meta(filename=filename)},
    )


def patch(client, location: str, offset: int, data: bytes):
    return client.patch(
        location,
        content=data,
        headers={**TUS, "Upload-Offset": str(offset), "Content-Type": "application/offset+octet-stream"},
    )


def upload_file(client, path: Path, filename: str, chunk: int = 64 * 1024) -> str:
    data = path.read_bytes()
    r = create_upload(client, len(data), filename)
    assert r.status_code == 201, r.text
    location = r.headers["Location"]
    for off in range(0, len(data), chunk):
        assert patch(client, location, off, data[off : off + chunk]).status_code == 204
    return location.rsplit("/", 1)[-1]


# --- tus protocol ---------------------------------------------------------------------------


def test_options(client):
    r = client.options("/api/uploads")
    assert r.status_code == 204
    assert r.headers["Tus-Version"] == "1.0.0"
    assert "creation" in r.headers["Tus-Extension"]


def test_upload_resume_flow(client):
    r = create_upload(client, 10)
    assert r.status_code == 201
    loc = r.headers["Location"]
    assert loc.startswith("/api/uploads/")

    assert client.head(loc, headers=TUS).headers["Upload-Offset"] == "0"
    assert patch(client, loc, 0, b"hello").headers["Upload-Offset"] == "5"
    # Client lost track and resumes: HEAD tells where to continue
    head = client.head(loc, headers=TUS)
    assert (head.headers["Upload-Offset"], head.headers["Upload-Length"]) == ("5", "10")
    assert patch(client, loc, 0, b"again").status_code == 409  # wrong offset
    assert patch(client, loc, 5, b"world").headers["Upload-Offset"] == "10"
    assert patch(client, loc, 10, b"!").status_code == 413  # beyond declared length

    assert client.delete(loc, headers=TUS).status_code == 204
    assert client.head(loc, headers=TUS).status_code == 404


def test_upload_rejections(client):
    assert client.post("/api/uploads", headers={"Upload-Length": "5"}).status_code == 412  # no Tus-Resumable
    assert create_upload(client, 5, "notes.txt").status_code == 415
    assert create_upload(client, 0).status_code == 400
    assert create_upload(client, 60 * 1024**3).status_code == 413
    loc = create_upload(client, 5).headers["Location"]
    bad_type = client.patch(loc, content=b"x", headers={**TUS, "Upload-Offset": "0"})
    assert bad_type.status_code == 415
    assert client.head("/api/uploads/../../etc", headers=TUS).status_code == 404


# --- job creation ---------------------------------------------------------------------------


def test_date_in_filename():
    from datetime import date

    assert date_in_filename("CT_V0001_20261104_1930.mxf") == date(2026, 11, 4)
    assert date_in_filename("newsdesk.mp4") is None
    assert date_in_filename("clip_202611041930.mp4") is None  # part of a longer number
    assert date_in_filename("x_20261340.mp4") is None  # not a date


def test_job_requires_complete_upload(client):
    loc = create_upload(client, 100).headers["Location"]
    r = client.post("/api/jobs", json={"upload_id": loc.rsplit("/", 1)[-1], "broadcast_date": "2026-11-19"})
    assert r.status_code == 409


def test_job_rejects_unreadable_file(client, tmp_path):
    f = tmp_path / "broken.mp4"
    f.write_bytes(b"\x00" * 1000)
    upload_id = upload_file(client, f, "broken.mp4")
    r = client.post("/api/jobs", json={"upload_id": upload_id, "broadcast_date": "2026-11-19"})
    assert r.status_code == 422
    assert "확인할 수 없습니다" in r.json()["detail"]


@needs_ffmpeg
def test_upload_to_analysis(client, synthetic_video):
    """DoD: an uploaded recording goes through proxy, alignment audio and signal analysis."""
    program = client.post("/api/programs", json={"name": "뉴스데스크", "broadcast_time": "19:30:00"}).json()
    upload_id = upload_file(client, synthetic_video.path, "CT_V0001_20261104_1930.mp4")

    r = client.post(
        "/api/jobs",
        json={"upload_id": upload_id, "program_id": program["id"], "broadcast_date": "2026-11-19"},
    )
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["program_name"] == "뉴스데스크"
    assert job["source_name"] == "CT_V0001_20261104_1930.mp4"
    assert (job["width"], job["height"], job["fps"]) == (320, 240, 25)
    assert any("2026-11-04" in w for w in job["warnings"])  # file name date ≠ broadcast date

    steps = {s["step"]: s["status"] for s in job["steps"]}
    assert steps["proxy"] == steps["analyze"] == "SUCCEEDED"
    assert steps["newsroom"] == "SUCCEEDED"  # fake newsroom (synthetic fixtures)
    assert steps["align"] == "SUCCEEDED"  # silent fake model: every report needs a manual range
    # No clip has a range, so there is nothing to describe or picture yet (M5 sets ranges).
    assert {steps[s] for s in ("metadata", "thumbnails")} == {"SUCCEEDED"}
    assert job["status"] == "REVIEW_READY"
    assert job["artifacts"] == {
        "proxy": True,
        "sprite": True,
        "waveform": True,
        "scenes": True,
        "silences": True,
    }

    analysis = client.get(f"/api/jobs/{job['id']}/analysis").json()
    assert [c["time"] for c in analysis["scenes"]] == pytest.approx(synthetic_video.cuts, abs=0.04)
    assert len(analysis["silences"]) == len(synthetic_video.silences)
    assert analysis["scene_threshold"] == 0.1

    # Proxy is served with HTTP Range (video seeking)
    r = client.get(f"/media/jobs/{job['id']}/proxy.mp4", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    assert client.get(f"/media/jobs/{job['id']}/sprite.json").json()["count"] == 8
    assert client.get(f"/media/jobs/{job['id']}/../../etc/passwd").status_code == 404
    assert client.get(f"/media/jobs/{job['id']}/align.wav").status_code == 404  # not public

    # The upload record is gone (the file moved into the job)
    assert client.head(f"/api/uploads/{upload_id}", headers=TUS).status_code == 404

    # Same file again → duplicate warning
    upload_id2 = upload_file(client, synthetic_video.path, "again.mp4")
    job2 = client.post("/api/jobs", json={"upload_id": upload_id2, "broadcast_date": "2026-11-19"}).json()
    assert any(f"#{job['id']}" in w for w in job2["warnings"])


def test_programs(client):
    r = client.post("/api/programs", json={"name": " 뉴스투데이 "})
    assert r.status_code == 201 and r.json()["name"] == "뉴스투데이"
    assert client.post("/api/programs", json={"name": "뉴스투데이"}).status_code == 409
    pid = r.json()["id"]
    r = client.put(f"/api/programs/{pid}", json={"name": "뉴스투데이", "audio_track": 1})
    assert r.json()["audio_track"] == 1
    assert [p["name"] for p in client.get("/api/programs").json()] == ["뉴스투데이"]
