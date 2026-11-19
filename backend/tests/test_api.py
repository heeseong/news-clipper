from __future__ import annotations


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["x-request-id"]


def test_readyz_checks_database(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["checks"]["database"] == "ok"


def test_job_list_starts_empty(client):
    assert client.get("/api/jobs").json() == {"items": [], "total": 0}


def test_pipeline_layout(client):
    stages = client.get("/api/pipeline").json()
    assert [s["name"] for s in stages] == ["processing", "aligning", "generating"]
    assert stages[0]["steps"] == ["proxy", "analyze", "newsroom"]


def test_dummy_job_runs_to_review_ready(client):
    r = client.post("/api/jobs/dummy", json={"seconds": 0})
    assert r.status_code == 201
    job = r.json()
    assert job["status"] == "REVIEW_READY"
    assert job["is_dummy"] is True
    assert {s["status"] for s in job["steps"]} == {"SUCCEEDED"}

    listed = client.get("/api/jobs").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == job["id"]
    assert "steps" not in listed["items"][0]


def test_failed_dummy_job_and_retry(client):
    job = client.post("/api/jobs/dummy", json={"seconds": 0, "fail_steps": ["align"]}).json()
    assert job["status"] == "FAILED"
    assert job["current_step"] == "align"

    r = client.post(f"/api/jobs/{job['id']}/steps/align/retry")
    assert r.status_code == 200
    assert r.json()["status"] == "REVIEW_READY"


def test_retry_errors(client):
    job = client.post("/api/jobs/dummy", json={"seconds": 0, "fail_steps": ["proxy"]}).json()
    assert client.post(f"/api/jobs/{job['id']}/steps/nope/retry").status_code == 404
    assert client.post(f"/api/jobs/{job['id']}/steps/align/retry").status_code == 409
    assert client.post("/api/jobs/999/steps/proxy/retry").status_code == 404


def test_dummy_job_rejects_unknown_steps(client):
    r = client.post("/api/jobs/dummy", json={"fail_steps": ["upload"]})
    assert r.status_code == 422


def test_get_missing_job(client):
    assert client.get("/api/jobs/12345").status_code == 404
