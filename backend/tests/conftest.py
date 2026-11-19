"""Shared fixtures.

The database is SQLite by default so tests need nothing installed. Set TEST_DATABASE_URL to a
PostgreSQL URL (CI does) to run the same tests against PostgreSQL; its tables are dropped and
recreated per test. Celery runs eagerly (in-process), so no Redis is needed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ["CELERY_ALWAYS_EAGER"] = "true"
os.environ["LOG_FORMAT"] = "console"
os.environ["DUMMY_STEP_SECONDS"] = "0"


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def app_env(db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point settings, engine and Celery at a fresh test database."""
    from app.core import config, db

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()

    import app.models  # noqa: F401
    from app.worker import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

    engine = db.get_engine()
    db.Base.metadata.drop_all(engine)
    db.Base.metadata.create_all(engine)
    yield
    db.Base.metadata.drop_all(engine)
    engine.dispose()
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()


@pytest.fixture
def client(app_env: None):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="session")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory):
    """An 8-second color/tone video with known cuts and silences (see tests/fixtures/synthetic.py)."""
    from tests.fixtures.synthetic import ffmpeg_available, make_synthetic_video

    if not ffmpeg_available():
        pytest.skip("ffmpeg not installed")
    return make_synthetic_video(tmp_path_factory.mktemp("media") / "synthetic.mp4")
