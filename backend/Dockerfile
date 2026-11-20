# API and Celery workers share the `app` image (the command differs per service).
# The align worker uses the `align` target: the same image plus PyTorch (CPU) and transformers.

# --- wheels: ctc-segmentation has no binary wheel on PyPI and needs a compiler ---
FROM python:3.12-slim AS wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /requirements.txt \
    && pip wheel --wheel-dir /wheels -r /requirements.txt

# --- app: API and the media / ai / publish workers ---
FROM python:3.12-slim AS app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so code changes do not reinstall them.
COPY --from=wheels /wheels /wheels
COPY --from=wheels /requirements.txt /tmp/requirements.txt
RUN pip install --no-index --find-links=/wheels -r /tmp/requirements.txt && rm -rf /wheels

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/storage /data/models \
    && chown -R app:app /data
USER app

ENV STORAGE_ROOT=/data/storage
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# --- align: + the acoustic model's libraries (the model itself is downloaded on first use
# into HF_HOME, a volume, so the image stays small and the 1.2 GB download happens once) ---
FROM app AS align
USER root
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY pyproject.toml /tmp/pyproject.toml
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('/tmp/pyproject.toml', 'rb'))['project']['optional-dependencies']['model']))" > /tmp/model.txt \
    && pip install -r /tmp/model.txt
USER app
ENV HF_HOME=/data/models HF_HUB_DISABLE_XET=1
