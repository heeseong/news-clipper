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

http://localhost:8080 에서 작업 목록이 열린다. 아직 업로드 기능이 없으므로(M1) **더미 작업 실행** 버튼으로 파이프라인을 확인한다. 더미 작업은 각 단계가 몇 초씩 대기만 하며, 특정 단계를 실패시키거나(수동 재시도 확인) 일시 오류를 내서(자동 재시도 확인) 볼 수 있다. 운영에서는 `ENABLE_DEV_ENDPOINTS=false`로 끈다.

## 파이프라인

```
[proxy | analyze | newsroom] → [align] → [metadata | thumbnails] → REVIEW_READY
   media   media     ai          align       ai        media
```

- 대괄호 안의 단계는 병렬로 실행되고, 모두 성공해야 다음 묶음으로 넘어간다 (Celery chord).
- 단계 정의: `backend/app/pipeline/definition.py`. 상태 전이: `orchestrator.py`. Celery 태스크: `tasks.py`.
- 단계 처리기는 `backend/app/pipeline/steps.py`의 `@register("proxy")`로 등록한다. M0에는 더미 처리기만 있다.
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

## API (M0)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/healthz` | 프로세스 생존 확인 |
| GET | `/readyz` | DB·Redis 연결 확인 (실패 시 503) |
| GET | `/api/pipeline` | 단계 구성 |
| GET | `/api/jobs`, `/api/jobs/{id}` | 작업 목록 / 상세(단계 포함) |
| POST | `/api/jobs/{id}/steps/{step}/retry` | 단계부터 재실행 |
| POST | `/api/jobs/dummy` | 더미 작업 생성 (개발용) |

API 문서: http://localhost:8000/docs (로컬 실행 시)
