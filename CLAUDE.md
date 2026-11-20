# News Clipper

종합뉴스 녹화본을 큐시트·원고(MARS-L)에 맞춰 리포트 단위로 자르고, 검수를 거쳐 YouTube에 예약 업로드하는 사내 웹앱.
사용자와는 **한국어**로 대화한다.

## 먼저 읽을 것
- `spec.md` — 기능 명세(v1.0). 정렬·경계 규칙의 근거는 5.5절
- `plan.md` — 마일스톤과 진행 상태. M0·M1 완료(로그인 보류), M2 완료(실제 MARS-L 호출까지 확인), 다음 작업은 **M3(정렬)**. M2 절의 "M2에서 정한 것"에 큐시트 대기·재조회 규칙이 있다
- `eval/poc/RESULTS.md` — 정렬 PoC 결과와 방송별 실패 사례

## 핵심 결정 (바꾸기 전에 사용자와 확인)
- 경계 탐지는 STT가 아니라 **원고-오디오 강제 정렬**(로컬 CPU, 오디오를 사외로 보내지 않음)
- 업로드 대상은 큐시트 형식 `R` 중 **자사 리포트만**. 단신, 헤드라인, 타이틀은 제외. 광주MBC·여수MBC 리포트도 제외
- 리포트 클립 = 리드 첫 문장(앵커 인사말 제외) ~ 다음 아이템 앵커 샷 컷
- 모든 클립은 검수·승인 후 업로드. 정렬 판정에 실패한 리포트는 자동으로 자르지 않고 검수자가 직접 지정
- 스택: Python 3.12 / FastAPI / Celery+Redis / PostgreSQL / FFmpeg, React+Vite+TS, Docker Compose
- 외부 연동(MARS-L, Claude, YouTube)은 인터페이스 + Fake 구현을 먼저 만든다

## 저장소 구조 (현재)
- `backend/app/core/` — 설정(`config.py`, 환경변수), JSON 로깅, DB 세션
- `backend/app/models/` — SQLAlchemy 모델(User, ProgramProfile, Job, JobStep). 바꾸면 Alembic 마이그레이션(`backend/alembic/`)도 만든다
- `backend/app/pipeline/` — 단계 정의(`definition.py`), 상태 전이(`orchestrator.py`, Celery 없음), Celery 태스크(`tasks.py`), 단계 처리기(`handlers.py`, `@register("proxy")`). 처리기가 없는 단계는 SKIPPED
- `backend/app/media/` — ffprobe·ffmpeg 래퍼(`ffmpeg.py`), 프록시·스프라이트·파형(`proxy.py`), 정렬용 오디오·장면 전환·무음(`analysis.py`)
- `backend/app/uploads.py`, `api/uploads.py` — tus 업로드. `intake.py` — 업로드 → 작업 생성(ffprobe 검증, 경고). `storage.py` — 저장 경로
- `backend/app/core/events.py`, `api/events.py` — Redis pub/sub → SSE 작업 변경 알림
- `backend/app/api/` — FastAPI 라우터. `backend/app/worker.py` — Celery 앱(큐 media/align/ai/publish)
- `backend/app/newsroom/` — 원고 파서(`script_parser.py`), 큐시트 아이템 판별·제목 정리·출처 판별(`rundown.py`), 모델(`models.py`), Provider 인터페이스·정규화(`provider.py`), MARS-L 클라이언트(`marsl.py`), Fake(`fake.py` + 합성 `fixtures/`), 큐시트 매칭(`matching.py`), 조회·저장(`service.py`). 설정 `NEWSROOM_PROVIDER=fake|marsl`
- `backend/app/models/rundown.py` — Rundown, RundownItem (작업당 큐시트 1개)
- `backend/app/align/korean_text.py` — 정렬용 한글 발음 변환(숫자, 영문, 발음 사전)
- `backend/tests/` — 단위 테스트. `tests/fixtures/synthetic.py`는 FFmpeg 합성 테스트 영상(컷·무음 위치를 알고 있음)
- `frontend/` — React + Vite + TS. `src/api/`(API 클라이언트·TanStack Query 훅), `src/pages/`
- `docker-compose.yml` — 서버 한 대 배포(postgres, redis, migrate, api, 워커 4개, web)
- `eval/tools/` — MARS-L 출력물(PDF) → API 형식 JSON 변환기(API 키를 받기 전 임시용), 파싱 검토 리포트 생성
- `eval/poc/` — 정렬 PoC 스크립트(`compute_logits.py`, `align_poc.py`). M3에서 `backend/app/align/`으로 옮긴다

## 명령
```bash
cd backend && uv venv && uv pip install -e ".[dev]"
cd backend && .venv/bin/python -m pytest -q   # SQLite + Celery 즉시 실행. 외부 서비스 불필요
cd backend && .venv/bin/ruff check .
cd frontend && npm install && npm run lint && npm run build
docker compose up -d --build                  # 전체 스택, http://localhost:8080
python eval/tools/marsl_pdf_export.py CUESHEET.pdf SCRIPTS.pdf eval/data/<날짜>-<프로그램>
python eval/tools/inspect_rundown.py eval/data/<날짜>-<프로그램>
```
정렬 PoC 실행 방법은 `eval/poc/RESULTS.md`와 `eval/poc/requirements.txt`를 본다.

## 데이터 규칙
- 실제 방송 자료(녹화본, 큐시트·원고 PDF, 정답, 클립)는 `eval/data/`에 두고 **커밋하지 않는다**(.gitignore). 세션이 바뀌면 사라지므로 필요할 때 사용자에게 다시 받는다
- 테스트에는 합성 데이터만 쓴다

## 환경 메모 (클라우드 세션)
- 이 저장소는 클라우드 환경 **news-clipper**에서 작업한다. Hugging Face, download.pytorch.org가 허용돼 있다
- PyTorch는 CPU 휠을 쓴다: `uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu`
- Hugging Face 모델은 `HF_HUB_DISABLE_XET=1`로 받는다. 모델은 `kresnik/wav2vec2-large-xlsr-korean`(1.2GB)
- deb.debian.org가 막혀 있어 세션 안에서는 백엔드 Docker 이미지(FFmpeg 설치)를 빌드할 수 없다. 이미지 빌드는 CI에서 확인한다. Docker 데몬은 `dockerd`로 직접 띄워야 한다
- `gh`는 GraphQL이 막혀 있다. PR 생성·병합은 REST(`gh api repos/heeseong/news-clipper/pulls ...`)로 한다
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
