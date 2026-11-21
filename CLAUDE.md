# News Clipper

종합뉴스 녹화본을 큐시트·원고(MARS-L)에 맞춰 기사(리포트·스트레이트) 단위로 자르고, 검수를 거쳐 YouTube에 예약 업로드하는 사내 웹앱.
사용자와는 **한국어**로 대화한다.

## 먼저 읽을 것
- `spec.md` — 기능 명세(v1.0). 정렬·경계 규칙의 근거는 5.5절
- `plan.md` — 마일스톤과 진행 상태. M0~M5 구현(로그인 보류, M3는 원래 골든셋 4편 재현, M4는 실제 Claude 호출·HD 녹화본 확인이 남음, M5 채널 선택은 M7 뒤). 다음 작업은 **M6(최종 렌더)**: M6 절의 **"M6 시작 가이드"**에 지금 상태, 연결점, 만들 것, 주의점, 사용자에게 받을 것이 정리돼 있다. 각 마일스톤의 "정한 것"에 규칙이 있다
- `eval/poc/RESULTS.md` — 정렬 PoC 결과와 방송별 실패 사례

## 핵심 결정 (바꾸기 전에 사용자와 확인)
- 경계 탐지는 STT가 아니라 **원고-오디오 강제 정렬**(로컬 CPU, 오디오를 사외로 보내지 않음)
- 업로드 대상은 큐시트 형식 `R`(리포트)과 `S`(스트레이트·단신) 중 **자사 기사** (2026-11-20 변경, 이전에는 리포트만). 헤드라인(`주요뉴스`), 타이틀, 클로징은 제외. 광주MBC·여수MBC 기사도 제외
- **뉴스투데이는 전날 뉴스데스크에서 나간 기사를 뺀다**(2026-11-20). 같은 기사 판별: 기사 ID(`articleId`·`orgArticleId` 교차) → 정규화 제목 + 기자. 원고를 줄이거나 다시 편집했으면(원고 유사도 90% 미만) 새 기사로 업로드. 프로그램 프로필 `dedupe_program`(뉴스투데이 기본값 `뉴스데스크`), 코드는 `newsroom/dedupe.py`
- 클립 = 리드 첫 문장(앵커 인사말 제외) ~ 다음 아이템 앵커 샷 컷. 단신도 같은 규칙(사인오프 대신 마지막 문장. 말 없는 자료화면이 이어지면 최대 10초 뒤의 다음 앵커 컷까지)
- 모든 클립은 검수·승인 후 업로드. 정렬 판정에 실패한 기사는 자동으로 자르지 않고 검수자가 직접 지정
- 스택: Python 3.12 / FastAPI / Celery+Redis / PostgreSQL / FFmpeg, React+Vite+TS, Docker Compose
- 외부 연동(MARS-L, Claude, YouTube)은 인터페이스 + Fake 구현을 먼저 만든다
- 메타데이터 기본값은 원고에서, LLM(Claude) 결과는 후보로만. 유튜브 제목은 `원고 제목[목포MBC 뉴스데스크]`(2026-11-21, 프로필 `title_template`). LLM 실패는 경고만 남기고 단계는 성공. LLM에는 원고 텍스트만 보낸다
- 썸네일: 리포트는 대표 프레임 + 템플릿 이미지(나중에 교체) + 제목 두 줄, 단신은 앵커 샷 뒤 하단 제목 자막이 처음 뜬 화면 그대로(2026-11-20)

## 저장소 구조 (현재)
- `backend/app/core/` — 설정(`config.py`, 환경변수), JSON 로깅, DB 세션
- `backend/app/models/` — SQLAlchemy 모델(User, ProgramProfile, Job, JobStep, Rundown, RundownItem, Clip, AlignedSentence, ClipMetadata, Thumbnail, AuditLog). 바꾸면 Alembic 마이그레이션(`backend/alembic/versions/000N_설명.py`, 현재 0009)도 만든다
- `backend/app/pipeline/` — 단계 정의(`definition.py`), 상태 전이(`orchestrator.py`, Celery 없음), Celery 태스크(`tasks.py`), 단계 처리기(`handlers.py`, `@register("proxy")`). 처리기가 없는 단계는 SKIPPED
- `backend/app/media/` — ffprobe·ffmpeg 래퍼(`ffmpeg.py`), 프록시·스프라이트·파형(`proxy.py`), 정렬용 오디오·장면 전환·무음(`analysis.py`)
- `backend/app/uploads.py`, `api/uploads.py` — tus 업로드. `intake.py` — 업로드 → 작업 생성(ffprobe 검증, 경고). `storage.py` — 저장 경로
- `backend/app/core/events.py`, `api/events.py` — Redis pub/sub → SSE 작업 변경 알림
- `backend/app/api/` — FastAPI 라우터(jobs, uploads, programs, newsroom 미리보기, clips, events, media). `backend/app/worker.py` — Celery 앱(큐 media/align/ai/publish)
- `backend/app/newsroom/` — 원고 파서(`script_parser.py`), 큐시트 아이템 판별·제목 정리·출처 판별(`rundown.py`), 모델(`models.py`), Provider 인터페이스·정규화(`provider.py`), MARS-L 클라이언트(`marsl.py`), Fake(`fake.py` + 합성 `fixtures/`), 큐시트 매칭(`matching.py`), 조회·저장(`service.py`), 전날 방송분 제외(`dedupe.py`). 설정 `NEWSROOM_PROVIDER=fake|marsl`
- `backend/app/models/rundown.py` — Rundown, RundownItem (작업당 큐시트 1개)
- `backend/app/align/` — 정렬(M3): 발음 변환(`korean_text.py`), log-prob(`logprobs.py`), wav2vec2 모델(`acoustic.py`, torch는 선택 의존성 `[model]`), 아이템 순차 정렬·판정(`aligner.py`), 경계 결정(`boundaries.py`), 클립 생성·저장(`service.py`). 상수는 PoC 그대로 — 바꾸면 골든셋을 다시 돌린다
- `backend/app/models/clip.py` — Clip(DRAFT, 판정 실패면 구간 null), AlignedSentence. `models/metadata.py` — ClipMetadata, Thumbnail (Clip에 CASCADE)
- `backend/app/metadata/` — 메타데이터(M4): 클립 원고(`script.py`), 원고 기반 기본값(`defaults.py`), 사실성 검사(`factcheck.py`), LLM 인터페이스·Fake(`llm.py`), Claude(`claude.py`), 선택(`factory.py`, 설정 `LLM_PROVIDER=off|fake|claude`), 단계·재생성(`service.py`)
- `backend/app/thumbnails/` — 썸네일(M4): 후보 시각·추출·점수(`candidates.py`, 얼굴 YuNet `models/`), 오버레이(`overlay.py`, 폰트 `fonts/`), 단계(`service.py`)
- `backend/app/review/` — 검수(M5): 검수자(헤더 `X-Reviewer`, 로그인 전 임시), 잠금, 구간·메타 수정, 썸네일 선택·추가, 승인·반려·다시 열기, 재실행 보호, 감사 로그. 라우터 `api/review.py`
- `backend/tests/` — 단위 테스트. `tests/fixtures/synthetic.py`는 FFmpeg 합성 테스트 영상(컷·무음 위치를 알고 있음)
- `frontend/` — React + Vite + TS. `src/api/`(API 클라이언트·TanStack Query 훅), `src/pages/`(작업 목록·상세, 업로드, 클립 편집기 `ClipEditorPage`), `e2e/`(Playwright E2E, `npm run e2e`)
- `docker-compose.yml` — 서버 한 대 배포(postgres, redis, migrate, api, 워커 4개, web)
- `eval/tools/` — MARS-L 출력물(PDF) → API 형식 JSON 변환기(API 키를 받기 전 임시용), 파싱 검토 리포트 생성
- `eval/poc/` — 정렬 PoC 스크립트(`compute_logits.py`, `align_poc.py`). 서비스 코드는 `backend/app/align/`
- `eval/tools/score_alignment.py` — 서비스 정렬을 녹화본 한 편에 돌려 `ground_truth.json`과 비교(골든셋 회귀)

## 명령
```bash
cd backend && uv venv && uv pip install -e ".[dev]"
cd backend && uv pip install torch --index-url https://download.pytorch.org/whl/cpu && uv pip install -e ".[model]"   # 실제 정렬 모델(선택)
cd backend && .venv/bin/python -m pytest -q   # SQLite + Celery 즉시 실행. 외부 서비스 불필요
cd backend && .venv/bin/ruff check .
cd frontend && npm install && npm run lint && npm run build
cd frontend && npm run e2e                     # 업로드 → 검수 → 승인 E2E (Fake, 백엔드·Vite를 스스로 띄움)
docker compose up -d --build                  # 전체 스택, http://localhost:8080
python eval/tools/marsl_pdf_export.py CUESHEET.pdf SCRIPTS.pdf eval/data/<날짜>-<프로그램>
python eval/tools/inspect_rundown.py eval/data/<날짜>-<프로그램>
cd backend && .venv/bin/python ../eval/tools/score_alignment.py ../eval/data/<날짜>-<프로그램>   # 골든셋 채점
```
정렬 PoC 실행 방법은 `eval/poc/RESULTS.md`와 `eval/poc/requirements.txt`를 본다.

## 데이터 규칙
- 실제 방송 자료(녹화본, 큐시트·원고 PDF, 정답, 클립)는 `eval/data/`에 두고 **커밋하지 않는다**(.gitignore). 세션이 바뀌면 사라지므로 필요할 때 사용자에게 다시 받는다
- PyTorch 없이 전체 스택을 띄울 때는 `ALIGN_MODEL=fake:silent`(모든 아이템 미검출 → 수동 구간 지정)
- 테스트에는 합성 데이터만 쓴다. 테스트는 실제 정렬 모델을 불러오지 않는다(`conftest.py`가 무음 log-prob으로 바꿔 끼움)

## 환경 메모 (클라우드 세션)
- 이 저장소는 클라우드 환경 **news-clipper**에서 작업한다. Hugging Face, download.pytorch.org가 허용돼 있다
- PyTorch는 CPU 휠을 쓴다: `uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu`
- Hugging Face 모델은 `HF_HUB_DISABLE_XET=1`로 받는다. 모델은 `kresnik/wav2vec2-large-xlsr-korean`(1.2GB)
- deb.debian.org가 막혀 있어 세션 안에서는 백엔드 Docker 이미지(FFmpeg 설치)를 빌드할 수 없다. 이미지 빌드는 CI에서 확인한다. Docker 데몬은 `dockerd`로 직접 띄워야 한다
- `gh`는 GraphQL이 막혀 있다. PR 생성·병합은 REST(`gh api repos/heeseong/news-clipper/pulls ...`)로 한다
- Anthropic API 키는 아직 없다. 받을 때는 `request_credential`(호스트 `api.anthropic.com`, 헤더 `x-api-key`)로 받고 `LLM_PROVIDER=claude ANTHROPIC_API_KEY=proxy-injected`로 실행한다
- MARS-L 키는 환경의 네트워크 비밀(`api.marsl.co.kr`, 헤더 `Mars-Secret`)로 저장돼 있다(2026-11-20). 실제 API는 `NEWSROOM_PROVIDER=marsl MARSL_API_KEY=proxy-injected`로 호출한다(프록시가 실제 키를 넣는다. 키는 채팅으로 받지 않는다)
- 이미지에 Python 3.13, Node 22, FFmpeg 6, PostgreSQL 16, Redis 7, Chromium(Playwright)이 있다. 환경의 설정 스크립트가 새 세션 시작 때 `backend/.venv`(uv)와 `frontend/node_modules`(npm ci)를 설치한다(2026-11-19 추가). 없으면 위 "명령"의 `uv venv`, `npm install`로 설치한다
- 실제 워커·DB로 확인할 때 (테스트에는 필요 없음):
  ```bash
  P=/var/tmp/ncpg; mkdir -p $P && chown postgres $P
  (cd /tmp && runuser -u postgres -- /usr/lib/postgresql/16/bin/initdb -D $P/data -U postgres --auth=trust -E UTF8)
  (cd /tmp && runuser -u postgres -- /usr/lib/postgresql/16/bin/pg_ctl -D $P/data -o "-p 5432 -k /tmp" -l $P/log start)
  psql -h localhost -U postgres -c "create user newsclipper password 'newsclipper'" -c "create database newsclipper owner newsclipper"
  redis-server --daemonize yes
  cd backend && .venv/bin/alembic upgrade head
  .venv/bin/uvicorn app.main:app --port 8000 &        # STORAGE_ROOT=/var/tmp/ncstorage 권장
  .venv/bin/celery -A app.worker worker -Q media,align,ai,publish -c 2 &
  cd ../frontend && npx vite --port 5173 &              # /api, /media는 8000으로 프록시
  ```
  postgres는 root로 못 띄우므로 `runuser -u postgres`를 쓰고, 데이터 디렉터리는 scratchpad가 아니라 `/var/tmp`에 둔다(postgres 사용자가 scratchpad에 접근 못 함)
- 브라우저 확인은 Playwright(`require('playwright')`)로 한다. 합성 테스트 녹화본은 `ffmpeg -f lavfi`로 만든다(밝기가 다른 장면, 아래 주의점)

## PoC에서 배운 주의점 (구현 시 지킬 것)
- ctc-segmentation의 문장 구간은 앞뒤로 최대 0.5초 늘어나 있다. 첫·마지막 **글자 시각**을 쓴다
- 창 앞쪽에 관계없는 오디오가 길면 원고 전체가 그쪽에 몰려 정렬된다. 점수로는 안 보이고 **길이 비율**로만 드러난다 → 판정 실패 시 뒤의 컷부터 재정렬
- 첫 리포트에는 거의 항상 앵커 인사말이 있다. 와이드 샷에서 시작해 앵커 컷 뒤까지 이어지기도 한다 → 컷과 리드 사이 음절 수로 감지
- 장면 컷 검출은 임계값을 낮게(ffmpeg scene 0.1). 컷 프레임은 한 프레임짜리 블렌드일 수 있다(시작 +1프레임, 끝 -½프레임)
- 프레임 단위로 정확히 자르려면 출력 쪽에서 길이를 지정한다(`-ss A -i IN -t D`). 입력 쪽 `-to`는 한 프레임이 더 들어간다
- 신호 시각(컷, 무음, 정렬)은 모두 **파일 시작 기준**이다. 비디오만 쓰는 ffmpeg 실행(`-map 0:v -an`)은 비디오 스트림 시작 기준으로 재므로, 오디오가 먼저 시작하는 파일에서는 어긋난다 → 장면 검출 때도 오디오 스트림을 함께 매핑한다
- ffmpeg scene 점수는 밝기 변화를 본다. 색상만 바뀌는 합성 영상(testsrc2 + hue)으로는 컷이 안 잡히니 테스트 영상은 밝기가 다른 장면으로 만든다
- 숫자는 읽는 소리대로 바꾼다(고유어 수사: `8곳` → `여덟곳`). 철자와 다르게 읽는 단어는 발음 사전에 넣는다(`KIA` → `기아`)

## 작업 방식
- 커밋·푸시는 기능 브랜치에서 하고, `main`에는 PR로 합친다
- 실제 방송 데이터로 검증할 때는 정답을 먼저 정하고(블라인드) 결과를 본다. 결과를 보고 고쳤으면 그 사실을 기록한다
