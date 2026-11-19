# News Clipper

종합뉴스 녹화본을 큐시트·원고(MARS-L)에 맞춰 리포트 단위로 자르고, 검수를 거쳐 YouTube에 예약 업로드하는 사내 웹앱.

- 기능 명세: [`spec.md`](spec.md)
- 구현 계획·진행 상태: [`plan.md`](plan.md)
- 정렬 PoC 결과: [`eval/poc/RESULTS.md`](eval/poc/RESULTS.md)

## 구성

| 서비스 | 역할 |
|---|---|
| `web` | React SPA (nginx). `/api`는 `api`로 프록시 |
| `api` | FastAPI (`backend/app/main.py`) |
| `migrate` | 시작할 때 Alembic 마이그레이션 실행 후 종료 |
| `worker-media` | FFmpeg 작업 (프록시, 분석, 썸네일, 렌더) |
| `worker-align` | 원고-오디오 강제 정렬 (동시 실행 1) |
| `worker-ai` | MARS-L·Claude API 호출, 파이프라인 진행(오케스트레이션) |
| `worker-publish` | YouTube 업로드·상태 확인 |
| `postgres`, `redis` | DB, Celery 브로커·결과 저장 |

웹·API·워커·DB를 서버 한 대에서 함께 돌리는 구성이다. CPU를 많이 쓰는 media·align 워커는 동시 실행 수를 낮게 둬서 웹 응답이 느려지지 않게 한다(`.env`의 `*_CONCURRENCY`).

## 실행 (Docker Compose)

```bash
cp .env.example .env    # 비밀번호, 동시 실행 수 조정
docker compose up -d --build
```

http://localhost:8080 에서 작업 목록이 열린다. **업로드** 화면에서 녹화본을 끌어다 놓고 프로그램·방송일을 고르면 업로드(끊겨도 이어 올리기) → 영상 검사 → 프록시·분석이 자동으로 진행되고, 작업 화면에서 프록시를 재생하며 장면 전환·무음 목록을 볼 수 있다.

**더미 작업 실행** 버튼은 단계마다 몇 초씩 대기만 하는 작업을 만든다. 특정 단계를 실패시키거나(수동 재시도 확인) 일시 오류를 내서(자동 재시도 확인) 파이프라인 동작을 볼 수 있다. 운영에서는 `ENABLE_DEV_ENDPOINTS=false`로 끈다.

## 파이프라인

```
[proxy | analyze | newsroom] → [align] → [metadata | thumbnails] → REVIEW_READY
   media   media     ai          align       ai        media
```

- 대괄호 안의 단계는 병렬로 실행되고, 모두 성공해야 다음 묶음으로 넘어간다 (Celery chord).
- 단계 정의: `backend/app/pipeline/definition.py`. 상태 전이: `orchestrator.py`. Celery 태스크: `tasks.py`.
- 단계 처리기는 `backend/app/pipeline/handlers.py`에서 `@register("proxy")`로 등록한다. 처리기가 없는 단계는 `SKIPPED`(미구현)로 넘어간다.
  - `proxy`: 360p 프록시(검수 재생용, GOP 1초, 원본과 같은 fps·시간축), 파형, 그리고 같은 디코딩 패스에서 **원본 프레임으로** 1초 간격 스프라이트와 **장면 전환**(ffmpeg scene 0.1)을 만든다. 원본은 한 번만 디코딩·디인터레이스한다
  - `analyze`: 정렬용 오디오(16kHz mono WAV, 프로그램 프로필의 오디오 트랙 또는 전체 다운믹스), 무음 구간(-35dB, 0.25초 이상)
  - 시각은 모두 **파일 시작 기준 초**(정렬용 오디오와 같은 축). 비디오 스트림이 오디오보다 늦게 시작하는 파일이면 첫 프레임이 0이 아니다(`probe.json`의 `video_start`)
- 저장 위치: `storage/jobs/{id}/` (spec 6.2). 업로드 중인 파일은 `storage/uploads/`
- 일시 오류(`RetryableStepError`)는 지수 백오프로 자동 재시도하고, 그 밖의 오류는 단계를 `FAILED`로 만든다. 실패한 단계부터 다시 실행할 수 있다(`POST /api/jobs/{id}/steps/{step}/retry`). 앞 단계 결과는 그대로 둔다.

## 개발

### 백엔드
```bash
cd backend
uv venv && uv pip install -e ".[dev]"     # 또는 python -m venv .venv && pip install -e ".[dev]"
.venv/bin/python -m pytest -q             # SQLite + Celery 즉시 실행, 외부 서비스 불필요
.venv/bin/ruff check .
```
- PostgreSQL로 테스트: `TEST_DATABASE_URL=postgresql+psycopg://user:pw@localhost:5432/db pytest -q` (테이블을 지우고 다시 만든다)
- FFmpeg가 있으면 합성 테스트 영상(`tests/fixtures/synthetic.py`) 테스트도 실행된다
- 로컬 실행 (Postgres·Redis 필요, 설정은 `backend/.env` 또는 환경변수):
  ```bash
  alembic upgrade head
  uvicorn app.main:app --reload
  celery -A app.worker worker -Q media,align,ai,publish -c 2
  ```
- 모델을 바꾸면 마이그레이션을 만든다: `alembic revision --autogenerate -m "..."` → 생성된 파일 검토. `tests/test_migrations.py`가 모델과 마이그레이션이 일치하는지 확인한다

### 프론트엔드
```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, /api는 localhost:8000으로 프록시
npm run lint && npm run build
```

### CI
`.github/workflows/ci.yml`: 백엔드 lint + 테스트(SQLite, PostgreSQL), 프론트엔드 lint + 타입 검사 + 빌드, Docker 이미지 빌드.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/healthz` | 프로세스 생존 확인 |
| GET | `/readyz` | DB·Redis 연결 확인 (실패 시 503) |
| GET | `/api/pipeline` | 단계 구성 |
| GET | `/api/jobs`, `/api/jobs/{id}` | 작업 목록 / 상세(단계 포함) |
| POST | `/api/jobs/{id}/steps/{step}/retry` | 단계부터 재실행 |
| POST | `/api/jobs/dummy` | 더미 작업 생성 (개발용) |
| OPTIONS/POST/HEAD/PATCH/DELETE | `/api/uploads[/{id}]` | 녹화본 업로드 (tus 1.0, 50MB 청크) |
| POST | `/api/jobs` | 업로드 완료 파일로 작업 생성 (ffprobe 검증, 날짜·중복 경고) |
| GET | `/api/jobs/{id}/analysis` | 장면 전환·무음 |
| GET | `/api/events` | SSE: 작업 변경 알림 (Redis pub/sub) |
| GET/POST/PUT | `/api/programs` | 프로그램 프로필 (최소 기능, 관리 화면은 M9) |
| GET | `/media/jobs/{id}/{proxy.mp4,sprite.jpg,sprite.json,waveform.json}` | 프록시·스프라이트·파형 (Range 지원) |

API 문서: http://localhost:8000/docs (로컬 실행 시)
