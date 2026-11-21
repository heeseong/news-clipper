# News Clipper

종합뉴스 녹화본을 큐시트·원고(MARS-L)에 맞춰 기사(리포트·단신) 단위로 자르고, 검수를 거쳐 YouTube에 예약 업로드하는 사내 웹앱.

- 기능 명세: [`spec.md`](spec.md)
- 구현 계획·진행 상태·마일스톤별 결정: [`plan.md`](plan.md)
- 정렬 PoC 결과: [`eval/poc/RESULTS.md`](eval/poc/RESULTS.md)
- 개발 메모(작업 규칙, 세션 환경): [`CLAUDE.md`](CLAUDE.md)

## 흐름

```
녹화본 업로드(tus) → [프록시·장면 전환 | 정렬용 오디오·무음 | 큐시트·원고(MARS-L)]
  → 원고-오디오 강제 정렬(기사 구간) → [메타데이터 | 썸네일 후보] → 검수(REVIEW_READY)
  → 승인 → 원본 화질 렌더 → YouTube 업로드·예약 → 상태 확인·재시도·수정 반영
```

- 업로드 대상: 큐시트의 자사 리포트(R)·단신(S). 다른 방송사 기사, 헤드라인·타이틀·클로징은 제외. 뉴스투데이는 전날 뉴스데스크 방송분 제외
- 정렬은 로컬 CPU(wav2vec2)에서 한다. 오디오·영상은 사외로 보내지 않는다(외부 전송은 원고 텍스트(Claude)와 업로드 영상(YouTube)뿐)
- 모든 클립은 검수자가 승인해야 렌더·업로드된다. 정렬이 확실하지 않은 기사는 검수자가 구간을 직접 정한다

## 구성 (Docker Compose, 서버 한 대)

| 서비스 | 역할 |
|---|---|
| `web` | React SPA (nginx). `/api`, `/media`는 `api`로 프록시 |
| `api` | FastAPI (`backend/app/main.py`) |
| `migrate` | 시작할 때 Alembic 마이그레이션 실행 후 종료 |
| `worker-media` | FFmpeg 작업: 프록시, 분석, 썸네일, 최종 렌더 |
| `worker-align` | 원고-오디오 강제 정렬 (PyTorch 이미지, 동시 실행 1) |
| `worker-ai` | MARS-L·Claude 호출, 파이프라인 진행 |
| `worker-publish` | YouTube 업로드·상태 확인·수정 반영 |
| `beat` | 1분마다 업로드 재시도·상태 확인 (한 대만) |
| `postgres`, `redis` | DB, Celery 브로커 |

CPU를 많이 쓰는 media·align 워커는 동시 실행 수를 낮게 둬서 웹이 느려지지 않게 한다(`.env`의 `*_CONCURRENCY`).

## 실행

```bash
cp .env.example .env    # 비밀번호, 외부 연동, 동시 실행 수
docker compose up -d --build
```

http://localhost:8080 — 작업 목록, 녹화본 올리기, 게시 현황, 채널 화면.

주요 설정(`.env`, 전체는 `.env.example`과 `backend/app/core/config.py`):

| 설정 | 값 |
|---|---|
| `NEWSROOM_PROVIDER` | `marsl`(실제, `MARSL_API_KEY` 필요) / `fake`(합성 큐시트) |
| `ALIGN_MODEL` | 기본 `kresnik/wav2vec2-large-xlsr-korean`. `fake:silent` = 모델 없이(모든 기사 수동 구간) |
| `LLM_PROVIDER` | `claude`(`ANTHROPIC_API_KEY` 필요) / `fake` / `off`. LLM은 제목 후보 등 보조만 |
| `PUBLISHER` | `youtube`(`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY` 필요) / `fake` |
| `ENABLE_DEV_ENDPOINTS` | 운영에서는 `false` (더미 작업 버튼) |

YouTube 채널 연결: OAuth 클라이언트 유형 "TV 및 입력 제한 기기"를 만들고, 채널 화면에서 "Google 계정으로 연결" → google.com/device에 코드 입력. 자세한 절차는 `plan.md`의 M7 "실제 채널 확인 절차".

## 개발

### 백엔드
```bash
cd backend
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q             # SQLite + Celery 즉시 실행, 외부 서비스 불필요
.venv/bin/ruff check .
# 실제 정렬 모델(선택): uv pip install torch --index-url https://download.pytorch.org/whl/cpu && uv pip install -e ".[model]"
```
- PostgreSQL로 테스트: `TEST_DATABASE_URL=postgresql+psycopg://user:pw@localhost:5432/db pytest -q`
- 로컬 실행(PostgreSQL·Redis 필요): `alembic upgrade head`, `uvicorn app.main:app --reload`, `celery -A app.worker worker -Q media,align,ai,publish -c 2`, `celery -A app.worker beat`
- 모델을 바꾸면 마이그레이션을 만든다(`alembic revision --autogenerate`). `tests/test_migrations.py`가 일치를 확인한다
- 테스트에는 합성 데이터만 쓴다. 실제 방송 자료는 `eval/data/`(커밋하지 않음)

### 프론트엔드
```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, /api·/media는 localhost:8000으로 프록시
npm run lint && npm run build
npm run e2e      # 업로드 → 검수 → 승인 → 렌더 → (가짜) 업로드 → 수정 반영
```

### CI
`.github/workflows/ci.yml`: 백엔드 lint + 테스트(SQLite, PostgreSQL), 프론트엔드 lint·타입 검사·빌드, E2E, Docker 이미지 빌드.

## API

주요 엔드포인트는 `spec.md` 8장, 전체는 http://localhost:8000/docs (로컬 실행 시).
