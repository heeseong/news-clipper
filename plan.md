# News Clipper — 구현 계획 (plan.md)

> spec.md v1.3 기준 (큐시트·원고 + 강제 정렬, 자사 리포트·스트레이트 업로드 — 2026-11-20 스트레이트 추가, YouTube 업로드 2026-11-21). 각 마일스톤은 독립적으로 실행·검증할 수 있는 단위이며, 완료 조건(DoD)을 만족하면 다음으로 넘어간다.

## 진행 원칙
- 각 단계는 "핵심 로직 → 단위 테스트 → API → (필요 시) UI" 순서로 만든다.
- 외부 연동(MARS-L, Claude, YouTube)은 **인터페이스 + Fake 구현**을 먼저 만든다. 키나 접근 권한이 없어도 전체 파이프라인을 끝까지 돌릴 수 있어야 한다.
- **가장 불확실한 부분(한국어 강제 정렬 정확도)을 먼저 검증한다.** 녹화본 샘플이 오는 즉시 M-PoC를 다른 마일스톤과 병행한다.
- 테스트 영상: FFmpeg로 합성한 짧은 영상(색 화면 + 톤 + 장면 전환 + 무음)을 `tests/fixtures`에서 생성해 CI에 쓴다. 실제 방송 자료(녹화본, 큐시트, 원고)는 **저장소에 커밋하지 않고** 로컬 평가에만 쓴다.
- 작업 단위: 마일스톤 안의 Task 하나 = 커밋(또는 PR) 하나

## 디렉터리 구조 (목표)
```
news-clipper/
  spec.md, plan.md, docker-compose.yml, .env.example
  backend/
    app/
      api/            # FastAPI 라우터
      core/           # 설정, DB, 보안, 로깅
      models/, schemas/
      pipeline/       # ingest, proxy, signals, newsroom, align, clip, metadata, thumbnail, render, publish
      newsroom/       # NewsroomProvider(MARS-L, Fake), 원고 파서
      align/          # 문장 분할, 강제 정렬, 대략 위치 추정, 경계 보정
      metadata/, thumbnails/, review/, render/  # 메타·썸네일(M4), 검수(M5), 렌더(M6)
      publish/        # Publisher(YouTube, Fake), OAuth, 할당량, 업로드 흐름(M7)
      media/          # ffmpeg/ffprobe 래퍼, 장면·무음 분석
      worker.py
    alembic/
    tests/
  frontend/src/
  eval/               # 정렬 평가 스크립트, 골든셋 라벨 형식 (실제 데이터는 커밋 제외)
```

---

## M-PoC. 강제 정렬 검증 — 완료 (2026-11-18)
결과와 근거: `eval/poc/RESULTS.md`. 코드: `eval/poc/` (PoC 스크립트), `backend/app/align/korean_text.py`.

- 방송 4편(11-02, 11-04, 11-05, 11-11), 업로드 대상 리포트 17건, 경계 34개 모두 앵커 샷 컷과 프레임 단위로 일치
- 블라인드 실행: 11-04 8/10(틀린 2개는 경고로 걸러짐), 11-05 9/10(1개는 경고 없이 통과 → 인사말 규칙 추가)
- 큐시트·녹화본 날짜 불일치(11-03 큐시트 + 11-04 영상): 리포트 전부 거부
- 확정 사항: 모델 `kresnik/wav2vec2-large-xlsr-korean`, CPU(4코어)에서 실시간의 약 6배, `ctc-segmentation`
- 남은 것: STT 대조 실험(P.4c)은 하지 않음(필요성 낮음). 프로그램 끝(클로징·광고 직전 리포트), 원본 화질, 실제 MARS-L API 응답으로는 아직 검증하지 않음

---

## M0. 프로젝트 기반 — 완료 (2026-11-19)
> 로그인(0.7)은 M0에서 빼고 필요할 때 추가한다. `User` 모델과 역할(Role)만 만들어 뒀다.

| # | Task | 상태 |
|---|---|---|
| 0.1 | 디렉터리 구조, `.env.example`, README | 완료 |
| 0.2 | Docker Compose: postgres, redis, migrate, api, worker(media/align/ai/publish), web. FFmpeg 포함 이미지 | 완료 |
| 0.3 | FastAPI 골격, 설정(pydantic-settings), 구조화 로깅(JSON), `/healthz`·`/readyz` | 완료 |
| 0.4 | SQLAlchemy + Alembic, 기본 모델(User, ProgramProfile, Job, JobStep) | 완료 |
| 0.5 | Celery 앱, 큐 4개, 오케스트레이터(병렬 그룹 → 합류 → 다음 단계, JobStep 기록, 자동 재시도·단계별 수동 재시도) | 완료 |
| 0.6 | React + Vite + TS 골격, API 클라이언트, 라우팅, 레이아웃, 작업 목록·상세 화면 | 완료 |
| 0.7 | 로그인, 역할 기반 권한 | **보류** (필요할 때 추가) |
| 0.8 | pytest, 합성 테스트 영상 fixture, CI(lint + test) | 완료 |

**DoD**: `docker compose up` 후 빈 작업 목록이 보이고, 더미 Job이 병렬·순차 단계를 거치며 상태가 바뀐다. (로그인은 0.7 보류로 제외)

**검증 (2026-11-19, 클라우드 세션)**
- `docker compose up`으로 전체 스택을 띄워 빈 목록 → 더미 작업 → REVIEW_READY까지 확인. 단계별 로그가 해당 큐의 워커에서 나오는 것도 확인
- 단, 세션 네트워크에서 deb.debian.org가 막혀 있어 **FFmpeg를 뺀 백엔드 이미지**로 확인했다. FFmpeg 설치를 포함한 이미지 빌드는 CI(Docker 작업)에서 처음 확인된다
- 실제 Celery 워커 + Redis + PostgreSQL에서 병렬 실행, 합류, 일시 오류 자동 재시도, 실패 → 수동 재시도 확인
- 진행률은 1초 폴링으로 보여준다. SSE는 M1(1.8)

**M0에서 정한 것**
- 파이프라인 단계: `[proxy | analyze | newsroom] → [align] → [metadata | thumbnails]`. `analyze`는 spec 3단계(정렬용 오디오, 장면 전환, 무음). 단계 이름이 바뀌면 `backend/app/pipeline/definition.py`만 고친다
- 오케스트레이션 콜백은 `ai` 큐에서 돈다(큐를 4개로 유지하면서, 오래 걸리는 media·align 작업 뒤에 밀리지 않게)
- 단계 처리기는 오류를 Job·JobStep에 기록하고 정상 종료한다. 다음 단계로 갈지는 chord 콜백이 DB 상태를 보고 정한다
- 백엔드 이미지의 FFmpeg는 Debian 패키지(현재 7.x)를 쓴다. align 워커는 M3에서 PyTorch를 넣은 별도 이미지로 나눈다

---

## M1. 수집 + 프록시 + 신호 분석 (파이프라인 1~3) — 완료 (2026-11-19)
| # | Task | 상태 |
|---|---|---|
| 1.1 | tus 업로드 엔드포인트 + 저장소 경로 관리 | 완료 (tus 1.0 core·creation·termination, `storage/uploads/`) |
| 1.2 | 프론트: 드래그 앤 드롭 업로드(tus-js-client), 진행률, 재개, 프로그램·방송일 입력 | 완료 (속도·남은 시간, 일시 정지, 새로고침 후 이어 올리기) |
| 1.3 | ffprobe 검증, 해시 중복 경고, Job 생성 | 완료 (+ 파일 이름·메타데이터 날짜 경고) |
| 1.4 | `media/ffmpeg.py` 래퍼: 명령 생성, 진행률 파싱(`-progress pipe:1`), 타임아웃, 오류 처리 | 완료 |
| 1.5 | 프록시 변환(360p, GOP 1초, 디인터레이스 자동 감지) + 스프라이트 + 파형 JSON | 완료 (스프라이트는 원본 프레임에서) |
| 1.6 | 정렬용 오디오 추출(16kHz mono WAV, 트랙 선택·다운믹스) | 완료 |
| 1.7 | 장면 전환, 무음(silencedetect) 분석 → JSON | 완료 (PySceneDetect 대신 PoC에서 검증한 ffmpeg scene 0.1) |
| 1.8 | SSE 진행률 + 작업 목록·상세 화면의 단계별 진행 표시 | 완료 (Redis pub/sub → `/api/events`, 끊기면 폴링) |

**DoD**: 영상을 끌어다 놓으면 업로드 → 프록시 → 오디오 → 신호 분석이 자동 실행되고, 작업 상세에서 프록시를 재생할 수 있다. 합성 영상으로 장면 전환·무음 탐지 테스트가 통과한다.

**검증 (2026-11-19, 클라우드 세션 4코어)**
- 브라우저 E2E: 60초 1080i(29.97fps, 비월주사, 타임스탬프가 3600초부터 시작하는 TS) 합성 녹화본을 업로드 → 작업 생성 → 프록시·분석 → 검수 대기. 장면 전환 8개가 모두 프레임 단위로 맞고(180, 450, 495, … 프레임), 무음 3개도 맞음. 컷을 누르면 프록시가 그 시각으로 이동. 콘솔 오류 없음
- 업로드 도중 페이지를 새로고침하고 같은 파일을 다시 고르면 끊긴 위치(24,739,840바이트)부터 이어서 올림
- 실제 Celery 워커·Redis·PostgreSQL에서 SSE로 진행률이 갱신됨
- 실제 방송 녹화본(MXF 등)으로는 아직 확인하지 않음

**M1에서 정한 것·알게 된 것**
- 장면 전환은 프록시 인코딩과 **같은 ffmpeg 패스**에서 찾는다. 1080i에서는 디코딩+디인터레이스가 비싸서(60초에 약 7초), 따로 돌리면 두 번 하게 된다. 그래서 `proxy` 단계 = 프록시·스프라이트·파형·장면 전환, `analyze` 단계 = 정렬용 오디오·무음
- 프록시는 **360p**(검수 재생용, 사용자 요청으로 720p에서 변경). 스프라이트는 프록시가 아니라 원본 프레임에서 같은 디코딩 패스로 만든다. 유튜브 썸네일 후보(M4)와 최종 렌더(M6)도 원본에서 만든다
- 시각은 모두 **파일 시작 기준**(정렬용 오디오와 같은 축). 비디오만 쓰는 ffmpeg 실행은 비디오 스트림 시작 기준으로 재서, 오디오가 먼저 시작하는 파일에서 수십 ms 어긋난다(합성 TS에서 21ms 확인). `probe.json`에 `video_start`(파일 시작 대비 비디오 시작)를 기록한다. 프레임 번호는 비디오 첫 프레임부터 센다
- 아직 구현하지 않은 단계(newsroom, align, metadata, thumbnails)는 `SKIPPED`(화면: 미구현)로 넘어가고 작업은 검수 대기가 된다. 해당 마일스톤에서 처리기를 등록하면 바뀐다
- 디인터레이스 판단은 ffprobe의 `field_order`로 한다. 값이 없거나 틀린 파일이면 디인터레이스하지 않는다(실제 녹화본으로 확인 필요, 필요하면 `idet`로 보완)
- 처리 시간: 60초 1080i 합성 영상에서 프록시 단계 약 18초, 분석 단계 약 1초(4코어). 30분 방송이면 프록시 약 9분으로 spec 목표(8코어 3분)보다 길다. 실제 녹화본·서버에서 다시 재고, 부족하면 `PROXY_PRESET=ultrafast`, 하드웨어 인코딩을 검토한다(이 측정은 720p 프록시 기준. 360p로 바꾼 뒤 인코딩 부담은 줄었다)
- 프로그램 프로필은 업로드 폼에서 이름만 추가할 수 있다(오디오 트랙 등은 API `PUT /api/programs/{id}`). 관리 화면은 M9

---

## M2. 큐시트·원고 연동 및 파서 (파이프라인 4) — 완료 (2026-11-20)
> **상태 (2026-11-20)**: Fake 데이터로 DoD 달성. 업로드 폼 큐시트 미리보기, `newsroom` 단계 실행·저장, `WAITING_RUNDOWN`과 수동 재조회, 작업 상세의 큐시트·원고 표가 동작한다. 사용자가 준 실제 응답 JSON(001 11-01, 003 11-04)으로 파싱을 점검했다(아래). 실제 MARS-L API 호출(2.10)도 확인했다.

**실제 응답 점검 (11-04 뉴스데스크 003, 22아이템)**: 형식 R 5 / S 17이 `remark`·`articleTypeCodeName`과 모두 일치, 업로드 대상 5(R-1~R-5), 원고 추정 길이와 예정 길이 차이 0~12초. 이 데이터를 보고 고친 것: 기자 소개 `○○○ 기자 입니다.`(띄어 쓴 형태)를 intro로 인식, 제목 `최종)` 같은 편집 표시를 방송사로 오인하지 않음, 데스크 이름 대신 사인오프 기자 사용(경고 아님). 그 밖의 관찰: 목록(001)에는 `updateDatetime`이 없고 `inputDateTime`만 있다(→ `confirmed_at`), `channelName`이 목록은 `목포문화방송`, 기사는 `목포`, `remark`에 `어깨걸이` 같은 값도 온다(단신), 기사에 `orgArticleId`, `chDivId`, `orgChDivId`가 있다(명세서에 없음, 쓰지 않음). 002 실제 응답은 아직 받지 않았다(spec 3.1에 따르면 001 원소와 같은 필드)

| # | Task | 상태 |
|---|---|---|
| 2.1 | `NewsroomProvider` 인터페이스 + `FakeNewsroomProvider` (MARS-L 실제 응답 형식 그대로인 **합성** JSON 픽스처) | 완료 (`newsroom/provider.py`, `fake.py`, `fixtures/`). 픽스처 필드 구성·순서를 실제 응답(001: 11-01, 003: 11-04)에 맞춤 |
| 2.2 | `MarsLProvider`: IF_MARSL_001~003 클라이언트(004는 필요 없음, 아래), 인증 헤더 `Mars-Secret`, 공통 오류 응답 처리, 재시도(429·5xx·타임아웃), 필드 별칭·공백·날짜 형식 정규화, `programType == "TV"` 필터 | 구현됨 (`newsroom/marsl.py`, httpx MockTransport 테스트). 실제 서버 호출은 2.10 |
| 2.3 | Rundown / RundownItem DB 모델, 큐시트 매칭(방송일 + 프로그램명 trim + 시작 시각), 원본 응답 저장, `cueSheetVersion` 기반 최종판 확인·판 변경 차이 기록 | 완료 (`models/rundown.py`, 마이그레이션 0003, `newsroom/matching.py`, `service.py`). 정렬 직전 판 수 재확인은 M3 align 단계에서 `service.fetch`로 |
| 2.4 | **원고 파서**: 섹션, 인터뷰·스탠드업·사인오프 구분, 발화 아닌 요소 제거, 끊긴 줄 복원, 문장 분할 | 구현됨 (`script_parser.py`) |
| 2.5 | 메모 파싱(썸네일·상단·수퍼), 제목 정리(`R]`, `(R)` 등) | 구현됨. 단 실제 API의 `memo`는 비어 있음(spec 3.1) |
| 2.6 | **형식(R/S) 판별**: `remark` → `articleTypeCode`(003의 `article`에 들어 있음) → 원고 구조. 판별 근거 저장 | 구현됨 (`rundown.classify`). 004 매칭은 필요 없음 |
| 2.7 | **예정 길이**: `ancMentCttTime + artclCttTime + artclExtTime`, 없으면 원고 글자 수로 추정 | 구현됨 (`planned_from_article`, `estimate_seconds`) |
| 2.8 | 파서·판별 단위 테스트 | 구현됨 (`tests/test_script_parser.py`, `tests/test_newsroom.py`) |
| 2.9 | 업로드 폼의 큐시트 미리보기 API + UI (판 수, 리포트 수, 예정 총 길이, 큐시트 없으면 경고), `WAITING_RUNDOWN` 상태와 수동 재조회 | 완료. 자동 재조회(주기적으로 큐시트 등록 확인)는 하지 않음 — 필요하면 Celery beat로 추가 |
| 2.10 | 실제 API로 큐시트 조회해 파서 검증 (액세스 키 필요) | 완료 (2026-11-20). 클라우드 세션에서 `MarsLProvider`로 001(11-01, 11-04)·002·003(2086823) 호출 성공. 003 파싱 결과가 사용자 제공 샘플과 22아이템 모두 동일. 실제 API 모드로 미리보기 API(11-04 뉴스데스크 → #2086823, 2판, 업로드 대상 5)와 `newsroom` 단계(PostgreSQL 저장) 확인. 002의 필드는 001 원소와 같음 |
| 2.11 | 다른 방송사 리포트 제외(`isSameChannel`/`originChannelName` 우선, 제목 `광주)`, 기자 `이름(광주)`) | 완료. 제외 목록은 `ProgramProfile.excluded_sources`(기본 `광주`, `여수`, API `PUT /api/programs/{id}`) |
| 2.12 | 제목 정리 보완: `(R-1)`, `/기자명` 접미어 제거, 큐시트 기자(데스크)와 사인오프 기자가 다를 때 사인오프 기자 사용 | 완료 (`/기자명`은 그 아이템 기자 이름일 때만 지움. 사인오프 기자를 `reporter`로, 다르면 원래 큐시트 기자를 `cue_sheet_reporter`로 저장. 11-04 실제 응답에서 리포트 5건 중 4건이 데스크 이름이라 경고로 두지 않음) |

**DoD**: Fake 데이터로 업로드 폼에서 큐시트가 미리 보이고, 리포트 아이템의 원고가 리드·본문·사인오프·메모로 파싱돼 저장된다. 작업의 `newsroom` 단계가 SKIPPED가 아니라 실제로 실행된다. → **달성 (Fake)**

### M2에서 정한 것
- 큐시트를 못 찾으면(없음, 프로그램 미지정, 같은 이름이 여러 개인데 방송 시각으로 못 고름) 단계는 `WAITING`, 작업은 그 단계 묶음이 끝난 뒤 `WAITING_RUNDOWN`. 같은 묶음에서 실패한 단계가 있으면 `FAILED`가 우선
- `POST /api/jobs/{id}/rundown/refresh {cue_sheet_id?}`는 `newsroom` 단계부터 재실행한다(`retry_step`). 같은 묶음의 프록시·분석이 아직 실행 중이면 409 — 끝난 뒤 다시 조회
- 사용자가 고른 큐시트는 `Job.params.cue_sheet_id`에 저장하고 이름 매칭보다 우선한다
- 판 수나 큐시트가 바뀐 채 다시 가져오면 `Rundown.changes`에 추가·삭제·순서 변경·수정 아이템을 기록한다(키: `cueSheetItemId`)
- 저장: `jobs/{id}/newsroom/rundown.json`(002·003 `data` 원본), `scripts/{item_no}.json`(파싱 결과), DB `rundowns.raw`
- `NEWSROOM_PROVIDER=fake`(기본)는 픽스처 큐시트를 **모든 날짜에** 내준다(`FAKE_NEWSROOM_ANY_DATE`). 합성 녹화본을 아무 날짜로 올려도 매칭된다. 픽스처: 뉴스데스크 19:30(7아이템, 업로드 대상 4), 뉴스투데이 06:00, 라디오 1건

### M2 시작 가이드 (새 세션용)
> 2026-11-20 기준 아래 연결점은 모두 구현됐다. 남은 일은 "사용자에게 받아야 하는 것"(키, 실제 응답 샘플)뿐이다.

**먼저 읽을 것**: spec 3.1(MARS-L API, 실제 응답 특징), 3.2(출처 판별), 5.4(큐시트 매칭 규칙), 10(`WAITING_RUNDOWN`)

**이미 있는 코드 (M0·M1)와 연결점**
- 파이프라인에 `newsroom` 단계가 이미 있다: `backend/app/pipeline/definition.py`의 첫 묶음 `[proxy | analyze | newsroom]`, 큐는 `ai`. 지금은 처리기가 없어 `SKIPPED`로 넘어간다. `backend/app/pipeline/handlers.py`에 `@register("newsroom")`로 처리기를 추가하면 바로 실행된다
  - 처리기는 `StepContext`(job_id, attempt, job_dir, report)를 받는다. 일시 오류는 `RetryableStepError`(자동 재시도), 그 밖은 `StepError`
  - 처리기 패턴은 같은 파일의 `proxy_step`, `analyze_step` 참고. Job 정보는 `session_scope()`로 읽는다
- `Job`에 `program_id`, `broadcast_date`가 이미 있다(업로드 폼에서 입력). `ProgramProfile`에 `name`, `broadcast_time`, `excluded_sources`, `newsroom_program_id` 칼럼이 이미 있다 (`backend/app/models/`)
- `JobStatus.WAITING_RUNDOWN`은 정의만 돼 있고 오케스트레이터(`orchestrator.py`)는 아직 다루지 않는다. 큐시트를 못 찾았을 때 이 상태로 두고 수동 재조회(`POST /api/jobs/{id}/rundown/refresh`, spec 8)로 이어 가는 흐름을 설계해야 한다. 단계 실패(FAILED)와 다르게 보여야 한다
- 저장 경로: spec 6.2의 `jobs/{id}/newsroom/rundown.json, scripts/{item_no}.json`. 경로는 `backend/app/storage.py`의 `JobFiles`에 추가한다
- 업로드 화면(`frontend/src/pages/UploadPage.tsx`)에 프로그램 선택·방송일 입력이 있다. 큐시트 미리보기(2.9)는 여기에 붙인다
- 작업 상세(`frontend/src/pages/JobDetailPage.tsx`)에 큐시트 아이템 목록(형식, 출처, 업로드 대상 여부, 예정 길이)을 보여 주면 M3·M5 전에 결과를 눈으로 확인하기 좋다
- 파서 진입점: `newsroom.rundown.build_item(raw_003_item) -> RundownItem` (pydantic). DB 저장 모델은 이와 별도로 만든다(spec 7의 Rundown, RundownItem 칼럼)
- 설정 추가 위치: `backend/app/core/config.py`(`Settings`). MARS-L 기본 URL·키(`MARSL_API_KEY`)·Provider 선택(fake/marsl)을 여기에 둔다. 키는 로그에 남기지 않는다

**정한 것 (M0·M1)**: 외부 연동은 인터페이스 + Fake 먼저(CLAUDE.md). 모델을 바꾸면 Alembic 마이그레이션(`0003_...`, 파일 이름은 `backend/alembic/versions/000N_설명.py`), `tests/test_migrations.py`가 모델과 일치하는지 확인한다. 테스트 데이터는 합성만 쓴다

**사용자에게 받아야 하는 것**
- ~~MARS-L 액세스 키~~ → 2026-11-20 클라우드 환경 news-clipper의 네트워크 비밀(`api.marsl.co.kr`, 헤더 `Mars-Secret`)로 저장됨. 세션에서 실제 API를 쓸 때는 `NEWSROOM_PROVIDER=marsl MARSL_API_KEY=proxy-injected`로 실행한다(프록시가 실제 키로 바꿔 넣는다). 원래 메모: 실제 API 호출(2.10)에만 필요. Fake로 DoD까지는 키 없이 진행할 수 있다
  - 이 클라우드 환경(news-clipper)은 `api.marsl.co.kr`이 허용 호스트에 없고 저장된 키도 없다. 실제 호출이 필요해지면 환경에 호스트 추가를 요청하고, 키는 채팅이 아니라 자격 증명 입력 양식으로 받는다(헤더 `Mars-Secret`)
  - 앱 서버에서 `api.marsl.co.kr`로 나갈 수 있는지(IP 허용 목록 여부)는 미결 (spec 16)
- 실제 큐시트·원고(PDF 출력물 또는 API 응답 JSON): 파서 검증용. `eval/data/`에 두고 커밋하지 않는다. 지난 세션의 `eval/data/`는 남아 있지 않다

---

## M3. 정렬 (파이프라인 5) — 완료 (2026-11-20, `main` 병합: PR #10, #11). 원래 골든셋 4편 재현(3.8 일부)만 녹화본을 받으면 확인
> PoC 스크립트(`eval/poc/align_poc.py`)를 `backend/app/align/`의 서비스 코드로 옮기는 단계다. 규칙은 spec 5.5와 `eval/poc/RESULTS.md`를 따른다.
> **상태 (2026-11-20)**: 3.1~3.7 구현. 상수·판단 순서는 PoC 그대로 옮겼다(바꾸려면 골든셋을 다시 돌린다). 실제 모델로 정렬 경로 전체가 돈다(합성 영상으로 확인). 골든셋 녹화본과 정답이 없어 3.8은 남았다.

| # | Task | 상태 |
|---|---|---|
| 3.1 | 로그 확률 계산 모듈: 모델 로딩(align 워커 상주, `HF_HUB_DISABLE_XET=1`로 다운로드), 20초 청크 + 1초 문맥, 결과(lpz) 캐시 저장 | 완료 (`align/acoustic.py`, `logprobs.py`, 캐시 `analysis/logprobs.npz`). torch는 선택 의존성 `[model]`, Docker는 `align` 타깃 이미지 + 모델 볼륨 |
| 3.2 | 아이템 순차 정렬: 앞 아이템 마지막 문장을 기준점으로 함께 정렬, 첫·마지막 글자 시각 사용, 원고 없는 아이템 길이만큼 창 확장, 첫 아이템은 앞부분 여유 300초 | 완료 (`align/aligner.py`) |
| 3.3 | 판정: `LOW_CONFIDENCE`(문장 점수 중앙값 < -5 또는 -5 미만 30% 초과), `DURATION_MISMATCH`(예정 길이 ±20%), 판정 실패 시 뒤의 장면 컷부터 재정렬 | 완료 |
| 3.4 | 경계 결정: 리드 시작 무음 보정(`LEAD_ONSET_IN_SILENCE`), 앵커 컷 스냅(+1프레임), 인사말 감지(컷~리드 사이 음절 4개 이상 → 무음에서 시작), 끝 = 다음 아이템 앵커 컷 - ½프레임 | 완료 (`align/boundaries.py`) |
| 3.5 | 업로드 대상 리포트별 Clip(DRAFT) 생성, `NOT_FOUND`·판정 실패 리포트는 수동 지정 대상으로 생성, 방송 단위 불일치 경고(아이템 절반 이상 실패) | 완료 (`align/service.py`, `models/clip.py`, 마이그레이션 0004). `OVERLAP`도 표시. `ORDER_CHANGED`는 순차 정렬이라 검출하지 않음 |
| 3.6 | 평가 도구: 골든셋 정답 형식(`ground_truth.json`), 경계 오차·판정 결과 리포트 (`eval/poc/align_poc.py`의 채점 부분 이전) | 완료 (`eval/tools/score_alignment.py`, 서비스 코드를 그대로 호출) |
| 3.7 | 단위 테스트: 경계 결정·판정 로직을 합성 데이터(가짜 lpz, 컷·무음 목록)로 테스트. 실제 모델은 테스트에서 쓰지 않음 | 완료 (`tests/test_align.py`, `tests/fixtures/logprobs.py`) |
| 3.8 | 회귀 확인: 골든셋 4편을 다시 올려(`eval/data/`는 git에 없음) 34/34 유지 확인 | 진행 중. 새 골든셋 2편(블라인드, 서비스 코드, 실제 API 큐시트): 11-16 **10/10**, 11-19 **10/10** (RESULTS.md). 기존 4편은 큐시트만 API로 받아 둠, 녹화본과 정답 필요 |

**DoD**: 골든셋 4편에서 PoC와 같은 결과(34/34, 불일치 조합 전부 거부)가 서비스 코드로 재현된다.

### M3에서 정한 것
- **뉴스투데이 중복 제외 (2026-11-20, 사용자 결정)**: 큐시트를 가져올 때 프로그램 프로필 `dedupe_program`(뉴스투데이 기본 `뉴스데스크`)의 전날 큐시트를 함께 조회해, 같은 기사는 업로드에서 빼고 `RundownItem.dedupe_note`에 사유를 남긴다(`newsroom/dedupe.py`, 마이그레이션 0005). 판별 순서 기사 ID(`articleId`·`orgArticleId` 교차) → 정규화 제목 + 기자, 원고 유사도 90% 미만이면 재편집 = 새 기사. 전날 큐시트가 없으면 경고만, MARS-L 일시 오류면 단계 재시도. 비교한 큐시트 원본은 `rundown.json`·`rundowns.raw`의 `compared`. 헤드라인(`주요뉴스`) 제목 아이템은 기타로 분류. 실제 API 확인(11-05~11-20 뉴스투데이 7편): 모든 중복이 기사 ID(`orgArticleId`)로 잡혔고 재편집 사례는 없었음. 11-20은 23개 중 18개가 데스크 방송분, 업로드 4개(새 단신). 남은 업로드 대상은 데스크 기사와 원고 유사도 최대 0.34로 놓친 중복 없음
- **2026-11-20 업로드 대상 변경**: 자사 스트레이트(단신, S)도 업로드한다(`rundown.UPLOAD_TYPES`). 단신 클립은 리포트와 같은 경계 규칙, 사인오프 없음 경고는 리포트에만. 큐시트 집계에 `briefs`, `excluded`는 다른 방송사 R·S. 11-16/11-19 재채점: 리포트 10/10 그대로. 단신은 정답 없이 결과를 본 뒤 점검(비블라인드): 14건 모두 시작·끝이 무음 안, 시작 14/14·끝 12/14가 컷과 일치. 나머지 2건(11-19 S12, S13)은 다음 앵커 컷이 말 끝에서 3초 넘게 떨어져 마지막 문장 + 0.4초에서 끝났다
- **단신 끝 연장 (2026-11-20, 사용자 결정)**: 단신은 다음 앵커 컷이 마지막 문장 끝에서 3초 넘게(최대 10초) 떨어져 있어도, 그 사이에 말소리가 없으면(음절 4개 미만) 그 컷 - ½프레임까지 늘린다(`boundaries.silent_tail`). 예정 길이 판정은 말한 부분까지. 리포트는 ±3초 규칙 그대로. 재채점: 리포트 10/10 그대로, 11-19 S12가 다음 앵커 컷에서 끝남. S13은 다음 아이템(14)이 정렬되지 않아 그대로
- `align` 단계는 먼저 큐시트 판 수를 다시 확인하고, 바뀌었으면 다시 가져온다(spec 5.4). MARS-L 장애면 경고만 남기고 저장된 판으로 정렬
- 판정에 걸린 리포트(`LOW_CONFIDENCE`, `DURATION_MISMATCH`)와 `NOT_FOUND`는 클립 구간을 비워 두고(`start_sec`/`end_sec` = null) 정렬 제안 구간만 `suggested_*`에 남긴다 → 검수자가 지정(M5)
- `align`을 다시 돌리거나 큐시트를 다시 가져오면 그 작업의 클립·정렬 문장을 지우고 새로 만든다. M5에서 검수자가 수정한 클립을 보존해야 하면 이 규칙을 바꾼다
- 방송 단위 불일치·못 찾은 아이템은 작업 경고(`정렬: …`)로 보여 준다
- 발음 사전: 기본 사전(`korean_text.PRONUNCIATIONS`) + 프로그램 프로필 `pronunciations`(API `PUT /api/programs/{id}`)
- 관찰(11-16, 11-19 두 번, 단신만 해당, 규칙은 안 바꿈): 녹화본 끝에 관계없는 오디오가 있으면 마지막 쪽 단신이 그쪽으로 몰려 정렬된다(창 끝 쏠림). 녹화본 끝 가까이에 리포트가 오면 문제가 될 수 있다. 소수점 `5.8`을 앵커는 [오쩜팔]로 읽는다. 리포트에 영향이 생기면 다룬다
- 테스트의 합성 log-prob에서 ctc-segmentation은 문장 첫 글자를 앞 무음 쪽으로 당겨 놓는다(실제 데이터에서도 일어나 PoC가 `refine_onset`으로 보정한 현상). 테스트는 보정 후 시각으로 비교한다

## M4. 메타데이터·썸네일 (파이프라인 7) — 완료 (2026-11-21). 실제 Claude 호출(키 필요)과 HD 녹화본 확인이 남음
> **상태 (2026-11-20)**: 4.1~4.6 구현, Fake LLM과 합성 영상으로 파이프라인 끝(REVIEW_READY)까지 테스트. Anthropic API 키와 실제 녹화본이 없어 실제 Claude 응답·실제 화면의 썸네일 점수는 확인하지 못했다.

| # | Task | 상태 |
|---|---|---|
| 4.1 | 원고 기반 기본값: 제목(정리된 원고 제목), 설명(앵커 리드 + 템플릿 꼬리말), 태그(기본 태그 + 기자명), 썸네일 문구(원고 메모) | 완료 (`metadata/defaults.py`, `script.py`). 태그에 지역명(전남 22개 시군·광주·전남). 메모는 항상 비어 있어 썸네일 문구는 제목에서(15자 이내 단어 단위) |
| 4.2 | `LLMProvider` 인터페이스 + Claude 구현 + Fake: 대안 제목 2개, 본문 요약, 키워드·해시태그 (structured output) | 완료 (`metadata/llm.py`, `claude.py`, `factory.py`). 썸네일 문구도 LLM이 만든다. 실제 API 호출은 키를 받으면 확인 |
| 4.3 | 사실성 검사: LLM 결과의 고유명사·숫자가 원고에 있는지 확인 | 완료 (`metadata/factcheck.py`, 형태 기반 휴리스틱) |
| 4.4 | 썸네일 후보 추출(원본에서, 프록시 아님) + 점수(선명도/노출/얼굴, 앵커 샷 감점) → 상위 6개 | 완료 (`thumbnails/candidates.py`, 얼굴은 YuNet) |
| 4.5 | 썸네일 오버레이 렌더러(Pillow, 템플릿 JSON) → 1280×720 JPG ≤ 2MB | 완료 (`thumbnails/overlay.py`, 나눔고딕 ExtraBold 동봉) |
| 4.6 | 메타 재생성 API | 완료 (`POST /api/clips/{id}/metadata/regenerate`, `ai` 큐) |

**DoD**: 각 클립(자사 리포트·스트레이트, 구간이 있는 것)에 원고 기반 메타데이터, LLM 대안, 썸네일 후보 6개가 생기고 Job이 REVIEW_READY가 된다. Claude API 키가 없어도 원고 기반 기본값만으로 진행된다.

### M4에서 정한 것
- **기본값은 원고, LLM 결과는 후보**: 제목 기본값은 정리된 원고 제목을 프로그램 제목 형식에 넣은 것 — **`원고 제목[목포MBC 뉴스데스크]`**(2026-11-21 사용자 결정, spec 16-5 해결, 프로필 `title_template`, 기본 `{title}[{station} {program}]`). 100자를 넘으면 원고 제목 부분을 줄이고(`…`) 경고. LLM 대안 제목 2개(80자 이하)도 같은 형식으로 `title_candidates`에. 설명은 리드(단신은 원고 전체) + 꼬리말이고 LLM 요약은 `summary`에 따로 둔다(설명에 넣지 않음). 썸네일 문구는 아래 "썸네일 문구" 규칙
- **사실성 검사**: 숫자, 영문 단어, 지명·기관 접미사(시·군·도·청·대학교·병원 …)로 끝나는 말, 직함 앞 이름(○○○ 지사·씨 …)이 원고(띄어쓰기 무시)에 없으면 경고. 제목·요약·썸네일 문구는 경고만 하고, 키워드·해시태그는 뺀다. 형태소 분석기 없는 휴리스틱이라 오탐이 있을 수 있다(경고일 뿐)
- **LLM 실패는 단계를 실패시키지 않는다**: API 오류(SDK가 429·5xx·타임아웃을 지수 백오프로 재시도한 뒤), 거절, 형식 오류 2회 → 경고(`LLM 보완 실패 …`)와 원고 기반 기본값. `LLM_PROVIDER=claude`인데 키가 없으면 LLM 없이 진행. 토큰 사용량은 `ClipMetadata.llm_input_tokens`·`llm_output_tokens`
- **Claude 호출**: 모델 `claude-opus-5-5`(설정 `LLM_MODEL`), effort `medium`, JSON 스키마 structured output + Pydantic 검사(제목 100자, 썸네일 문구 공백 빼고 15자, 키워드 10개, 해시태그 3개, 요약 400자), 거절 시 서버 측 대체 모델(`fallbacks: "default"`, `LLM_REFUSAL_FALLBACK`). 원고 텍스트만 보낸다. 클립 4개씩 병렬
- **썸네일 후보**: 클립 구간 안의 컷 + 0.5초(짧은 샷은 가운데)와 균등 12개, 컷 ±0.15초는 제외. 점수 = 0.45 선명도(Laplacian 분산, 로그, 클립 안 최댓값 기준) + 0.25 노출 + 0.3 얼굴(YuNet, 화면의 4% 이상이면 만점) − 0.6 앵커 구간 − 1.0 어둡거나 밋밋함. 같은 샷에서는 한 장씩 먼저 고른 뒤 나머지를 점수순으로 → 상위 6개, 1위를 선택. 앵커 구간: 리포트는 리드 마지막 문장 끝(±2초 안의 컷이 있으면 그 컷)까지, 단신은 첫 샷. 가중치는 실제 녹화본으로 확인하지 않은 값
- **프레임**: 원본에서 입력 쪽 `-ss`(파일 시작 기준), 인터레이스면 yadif, 화소 비율 보정 후 1280×720에 맞추고 남는 부분은 검은 띠
- **썸네일 디자인 (2026-11-20, 사용자 결정, 채널 썸네일을 따름)**:
  - 리포트(`headline`): 프레임 위에 **템플릿 이미지**(1280×720 투명 PNG, 로고·띠 등)를 얹고 그 위에 큰 글씨 두 줄(첫 줄 노랑, 둘째 줄 흰색, 하단 정렬). 템플릿 파일은 사용자가 나중에 준다 → `PUT /api/programs/{id}/thumbnail-overlay`(PNG 원본을 요청 본문으로, 16:9·투명 영역 검사, `STORAGE_ROOT/branding/programs/{id}/report_overlay.png`, 프로필 `thumbnail_template.report.overlay_image`), `DELETE`로 제거, `GET /api/programs/{id}/thumbnail-preview`로 샘플 확인. 글자 영역은 `text_box: [x, y, w, h]`. 템플릿이 있으면 그림자(`shade`)는 기본 0. 템플릿이 없으면 임시 디자인(파란 모서리 띠 + "목포MBC NEWS" 글자, 파란 사선 띠)
  - 단신(`frame`): 글자를 그리지 않는다. **앵커 샷이 끝나고 자료화면으로 넘어간 뒤 하단 자막(제목)이 처음 뜬 화면**을 1순위로 쓴다(2026-11-20 사용자 결정, `candidates.scan_brief`): 클립 시작부터 0.5초 간격으로 보며, 화면 위쪽 65%가 첫 프레임(앵커)과 달라지면(흐린 64×36 회색 평균 차이 15 초과) 자료화면 시작, 그 뒤 자막 점수 0.5 이상인 첫 프레임 + 0.5초(자막이 계속 있으면). 앵커→자료화면 전환은 디졸브가 많아 장면 컷으로는 안 잡힌다(11-16: 앵커 0~2, 자료화면 23~100). 앵커 화면의 인용 자막은 무시된다. 30초 안에 못 찾으면 점수순. 2~6순위는 점수순 대안이고, 단신의 앵커 구간도 이 화면 변화로 정한다. 후보 점수에는 자막 점수(`candidates.caption_score`: 하단 70~95% 띠의 세로 획(글자) 밀도 − 화면 가운데 밀도, × 가로 테두리 선)를 넣어 단신에서만 가중치 1.0(얼굴 0.15). OCR 없이 모양으로 판단하므로 실제 녹화본으로 확인해야 한다
  - 프로필 `thumbnail_template`: 공통 키 + `report`/`brief` 하위 키(`overlay.DEFAULT_TEMPLATES`). 기본 폰트는 나눔고딕 ExtraBold(OFL, `thumbnails/fonts/`), 글자가 안 들어가면 줄이고 문구는 자르지 않는다. 2MB를 넘으면 JPEG 품질을 낮춘다
- **HD 녹화본 확인은 보류 (2026-11-21, 사용자 결정)**: 녹화본 첨부 크기 제한으로 320×240 축소본 확인으로 갈음하고 M5로 넘어간다. 원본 화질에서 자막 판정·화면 차이 경계값(0.5, 15)이 맞는지는 운영 데이터로 확인한다
- **단신 첫 자막 화면 규칙 확인 (2026-11-20, 같은 녹화본)**: 1순위 6/6이 앵커 샷 직후 자료화면에 제목 자막이 다 뜬 화면(자료화면 시작 후 약 1~1.5초). 장면 컷 기준이던 이전 방식은 디졸브를 놓쳐 첫 컷이 자료화면의 두 번째 샷(8~12초)이었다
- **단신 자막 프레임 확인 (2026-11-20, 11-16 뉴스데스크 녹화본, 320×240 사본)**: `eval/tools/check_thumbnails.py`. 자동 구간이 나온 단신 6건(2, 7~11번)의 프레임을 2초 간격으로 보고 제목 자막 유무를 먼저 표시(블라인드)한 뒤 서비스 코드로 후보를 골랐다. **1순위 6/6 자막 있음, 후보 36장 중 34장 자막 있음**(1장은 자막이 사라진 끝부분 — 샷마다 한 장씩 먼저 고르는 규칙으로 5~6순위에 들어옴, 1장은 2초 간격 표시의 경계), 프레임 판정(자막 점수 0.3 이상) 110장 중 109장 일치. 남은 1장은 앵커 화면의 인용 자막("부적절 인사 연내 시정할 것")으로, 판정기는 제목 자막과 다른 자막을 구분하지 못하지만 앵커 감점으로 후보에서 밀린다. 결과를 본 뒤 정답 3장(2-16, 9-19, 10-19)을 고쳤다: 시트를 잘못 읽어 자막 있음으로 표시했는데 원본 프레임에 자막이 없었다(`caption_labels.json`에 기록). 이 방송의 단신 자막은 흰 바가 아니라 반투명 어두운 띠 위 흰 글씨이고, 앵커 화면에는 없고 자료화면에만 있으며, 자료화면 첫 1~2초에는 아직 없다. 오른쪽 아래에 수어 통역 원이 항상 있다. 같은 녹화본에서 12·13번은 정렬 판정 실패, 14번 이후는 녹화본이 끝나(20분) 미검출
- **썸네일 문구**: 채널은 대부분 원고 제목을 그대로 쓴다. 리포트는 제목을 `…`/`...`/`..`에서 두 줄로 나눈다(인용부호 뒤의 말줄임표는 남김, `·`는 지명에 쓰여 나누지 않음). 말줄임표가 없으면 사실성 검사를 통과한 LLM 두 줄 문구(줄당 공백 빼고 16자), 없으면 길이를 맞춰 두 줄. 단신은 문구 없음. 칼럼 길이 255(마이그레이션 0007)
- **metadata·thumbnails 단계는 병렬**이라 오버레이(`thumbnail.jpg`)는 둘 중 나중에 끝난 쪽이 만든다(각자 커밋한 뒤 `render_selected`)
- 구간이 없는 클립(정렬 판정 실패·미검출)은 둘 다 건너뛴다. M5에서 구간을 지정하면 `regenerate_clip_metadata` 태스크와 썸네일 후보 생성(`thumbnails.service.make_candidates`)을 다시 부른다(썸네일 재생성 API는 M5에서)
- `align`을 다시 돌리면 클립과 함께 메타·썸네일 행(ON DELETE CASCADE)과 `clips/` 폴더를 지운다(SQLite는 클립 ID를 재사용함). 재생성 API는 공개 설정(privacy, publish_at)만 두고 덮어쓴다 — M5에서 검수자 수정 보존 규칙을 정한다
- 프로그램 API(`POST/PUT /api/programs`)에 `description_footer`(자리표시 `{station} {program} {date} {reporter} {reporter_credit} {title}`), `default_tags`, `thumbnail_template` 추가. 설명 꼬리말·기본 태그·썸네일 디자인은 임시 기본값이고 사용자에게 받아 프로필에 넣는다
- 의존성: `anthropic`, `pillow`, `opencv-python-headless`(5.x는 Haar cascade가 없어 YuNet ONNX 모델 232KB를 `thumbnails/models/`에 넣음, MIT)

### M4 시작 가이드 (새 세션용, 2026-11-20 작성 — M4 구현 전 기록)

**먼저 읽을 것**: spec 5.7(제목·설명·썸네일), 5.8의 "메타데이터 편집·썸네일"(M5에서 쓸 형태), 7(ClipMetadata, Thumbnail), 9(비용: LLM 토큰 기록), 10(LLM 응답 검증 실패 처리). 이 파일의 "M2에서 정한 것", "M3에서 정한 것"

**지금 상태 (M0~M3 완료)**
- 파이프라인: `[proxy | analyze | newsroom] → [align] → [metadata | thumbnails] → REVIEW_READY`. `metadata`(큐 `ai`)와 `thumbnails`(큐 `media`)는 처리기가 없어 SKIPPED로 넘어간다. `backend/app/pipeline/handlers.py`에 `@register("metadata")`, `@register("thumbnails")`로 추가하면 바로 실행된다(패턴은 같은 파일의 `align_step`, `newsroom_step`)
- 업로드 대상(바뀜): **자사 리포트(R)와 스트레이트(S)**. 뉴스투데이는 전날 뉴스데스크 방송분 제외, 헤드라인(`주요뉴스`)·타이틀·다른 방송사 기사 제외. 판별은 `newsroom/rundown.py`(`UPLOAD_TYPES`), `newsroom/dedupe.py`. 결과는 `RundownItem.upload`
- 클립: `backend/app/models/clip.py`의 `Clip`(DRAFT). 업로드 대상 아이템마다 하나. 정렬 판정에 실패했거나 녹화본에 없으면 `start_sec`/`end_sec`가 null(`needs_manual_range`) → 검수자가 지정(M5). **M4는 구간이 있는 클립만 처리하고, null 클립은 건너뛰되 M5에서 구간을 지정한 뒤 메타·썸네일을 다시 만들 수 있게 한다**(4.6 재생성 API와 같은 함수)
- `align`을 다시 돌리거나 큐시트를 다시 가져오면 그 작업의 클립을 지우고 새로 만든다(`align/service.store_result`, `newsroom/service.store`). ClipMetadata·Thumbnail은 Clip에 `ondelete=CASCADE`로 매달면 함께 지워진다. M5에서 검수자 수정 보존이 필요해지면 이 규칙을 바꾼다
- 클립의 원고: `Clip.rundown_item` → `RundownItem.parsed_script`(ParsedScript JSON: `sentences[section, kind, text, speaker]`, `signoff_reporter`, `notes`), `title`(정리된 제목), `title_raw`, `series`, `reporter`(사인오프 기자), `cue_sheet_reporter`(데스크), `planned_duration`. 문장 시각은 `AlignedSentence`(`/api/clips/{id}/sentences`)
- **원고 메모(썸네일·상단 문구)는 실제 API에서 항상 비어 있다**(`article.memo` = null, spec 3.1). 4.1의 "썸네일 문구(원고 메모)"는 사실상 없음 → 제목에서 만들거나 LLM이 만든다(spec 5.7 표의 마지막 줄)
- 프로그램 프로필(`models/program.py`)에 M4용 칼럼이 이미 있다: `description_footer`(Text), `default_tags`(JSON list), `thumbnail_template`(JSON dict), `default_channels`. 아직 API로 편집할 수 없다(`schemas/program.py`, `api/programs.py`에 추가). `title_cleanup_rules`, `signoff_pattern`은 쓰이지 않는 칼럼
- 저장 경로(spec 6.2): `jobs/{id}/clips/{clip_id}/thumb_candidates/*.jpg, thumbnail.jpg` → `backend/app/storage.py`의 `JobFiles`에 추가. 브라우저에 보여 줄 파일은 `PUBLIC_MEDIA`와 `api/media.py`
- 원본 영상에서 프레임 추출: `media/ffmpeg.py`(`run_ffmpeg`, `probe`), 스프라이트를 원본에서 뽑는 `media/proxy.py`가 참고. 디인터레이스는 `probe.interlaced`일 때 `analysis.DEINTERLACE`. **시각은 파일 시작 기준**(CLAUDE.md 주의점)
- 화면: 작업 상세(`frontend/src/pages/JobDetailPage.tsx`)의 "클립" 탭이 클립 목록을 보여 준다. M4에서는 여기에 제목·썸네일 후보를 붙여 결과를 눈으로 확인하면 된다(편집 UI는 M5)

**만들 것 (제안)**
- 모델: `ClipMetadata`(clip_id, title, title_candidates[], description, tags[], hashtags[], thumbnail_text, privacy, publish_at, llm 관련: provider·model·token 사용량·경고), `Thumbnail`(clip_id, frame_sec, path, score, overlay_text, selected). 마이그레이션 0006, `tests/test_migrations.py`가 일치를 확인
- `LLMProvider` 인터페이스 + `FakeLLMProvider` + Claude 구현(`backend/app/llm/` 등). 설정 `LLM_PROVIDER=fake|claude`, `ANTHROPIC_API_KEY`, 모델 ID. **Claude API를 쓰는 코드를 쓰기 전에 `claude-api` 스킬을 먼저 불러** 현재 모델 ID, structured output(JSON 스키마) 방식, SDK 사용법을 확인한다. 응답이 스키마에 안 맞으면 오류를 넣어 1회 재요청, 그래도 실패하면 원고 기반 기본값만 쓰고 단계는 성공(spec 10). 토큰 사용량 기록(spec 9). LLM에는 원고 텍스트만 보낸다
- 단신(S)은 리드만 있고 리포트 본문이 없다: 설명·요약 규칙을 리포트와 다르게(예: 단신은 원고 전체가 설명)
- 썸네일 후보: 클립 구간 안의 장면 전환 직후 프레임(`analysis/scenes.json`) + 균등 샘플 → 점수(선명도 Laplacian variance, 노출, 얼굴, **앵커 샷 감점**) → 상위 6개. 앵커 샷은 리드 구간(첫 문장~리드 끝, `AlignedSentence.section == "lead"`)으로 알 수 있다. 얼굴 검출 라이브러리(OpenCV 등)를 넣을지는 이미지 크기·의존성과 함께 정한다
- 오버레이 렌더러(Pillow): 1280×720 JPG ≤ 2MB, 템플릿 JSON(문구 위치·폰트·색·로고). **한글 폰트 파일이 필요**하다(Docker 이미지에 넣는 방법을 정할 것: 저장소에 OFL 폰트 포함 또는 빌드 때 다운로드. 세션에서는 deb.debian.org가 막혀 apt로 폰트를 설치할 수 없다)
- 4.6 재생성 API: `POST /api/clips/{id}/metadata/regenerate`(spec 8)
- 테스트: Fake LLM과 합성 영상(`tests/fixtures/synthetic.py`)으로. 실제 Claude API·모델은 테스트에서 부르지 않는다

**사용자에게 받을 것 / 정할 것**
- **Anthropic API 키**: 실제 LLM 호출(4.2 검증)에만 필요. 없어도 Fake로 DoD까지 진행. 받을 때는 채팅이 아니라 `request_credential`(호스트 `api.anthropic.com`, 헤더 `x-api-key`, 접두어 없음)로 받고, 실행할 때 `ANTHROPIC_API_KEY=proxy-injected`처럼 자리표시 값을 넣는다(MARS-L 키와 같은 방식, CLAUDE.md 환경 메모)
- spec 16-5 **제목 정책**: 원고 제목 그대로 vs 유튜브용으로 다듬은 제목을 기본으로
- 설명 꼬리말 템플릿, 기본 태그, 썸네일 디자인(로고 파일, 폰트, 색, 문구 위치) — 없으면 임시 기본값으로 만들고 나중에 프로필에서 바꾼다
- 실제 클립으로 썸네일을 확인하려면 녹화본 1편이 필요하다(`eval/data/`는 세션이 바뀌면 사라진다). 그때 정렬까지 돌리려면 torch와 모델이 필요하다: `cd backend && uv pip install torch --index-url https://download.pytorch.org/whl/cpu && uv pip install -e ".[model]"`(환경 설정 스크립트는 `.[dev]`만 설치), 모델은 첫 실행 때 받는다(1.2GB, `HF_HUB_DISABLE_XET=1`). 큐시트는 MARS-L API로 바로 받을 수 있다(`eval/tools/score_alignment.py`가 녹화본·`items.json`으로 신호·정렬까지 만든다)

**기존 남은 일 (M4와 별개)**
- M3.8: 원래 골든셋 4편(11-02, 11-04, 11-05, 11-11) 재현. 녹화본과 정답이 필요하다(새 골든셋 11-16, 11-19는 서비스 코드로 블라인드 10/10씩)
- 관찰만 한 것: 녹화본 끝의 관계없는 오디오 쪽으로 마지막 단신이 몰려 정렬됨(창 끝 쏠림), 소수점 `5.8`을 [오쩜팔]로 읽음(RESULTS.md)
- 큐시트 자동 재조회(주기적)는 없음. 필요하면 Celery beat

---

## M5. 검수 화면 (파이프라인 8) — 완료 (2026-11-21). 채널 선택은 M7에서 추가
> **상태 (2026-11-21)**: 5.1~5.8 구현. 11-16 녹화본·실제 큐시트·실제 정렬로 띄운 스택에서 편집 화면·타임라인·키보드 조작·잠금을 브라우저(Playwright)로 확인했고, E2E(업로드 → 구간 지정 → 메타 수정 → 승인, Fake)가 통과한다. "리포트 1건 1분 안에"는 사람이 써 보며 확인할 것.

| # | Task | 상태 |
|---|---|---|
| 5.1 | 작업 상세: 전체 타임라인(스프라이트 + 파형 + 아이템 블록 + 경고) + 큐시트 패널(검출 상태) | 완료 (`components/JobTimeline.tsx`: 클릭 = 이동, 블록 더블클릭 = 편집기. 큐시트 표에 검수 상태·편집기 링크) |
| 5.2 | 클립 편집기: 플레이어(프레임 이동, 단축키), IN/OUT 편집, 경계 반복 재생, 스냅 마커 | 완료 (`pages/ClipEditorPage.tsx`, `components/BoundaryStrip.tsx`) |
| 5.3 | 원고 패널: 정렬 문장 하이라이트, 클릭 이동, 신뢰도 표시 | 완료 (점수 -5 미만 문장 표시) |
| 5.4 | 메타데이터 폼(제목 후보, 글자 수), 썸네일 선택/현재 프레임/업로드, 오버레이 미리보기 | 완료 |
| 5.5 | 미검출 리포트 수동 구간 지정 → 클립 생성 | 완료 (클립은 업로드 대상마다 이미 있으므로 `PATCH /api/clips/{id}`로 구간 지정 → 메타·썸네일 생성) |
| 5.6 | 발행 설정(채널, 공개 범위, 예약 KST), 일괄 승인·순차 예약 | 완료 (채널 선택은 M7에서 추가) |
| 5.7 | 승인 검증, 편집 잠금, 감사 로그 | 완료 (`app/review/service.py`, `AuditLog`) |
| 5.8 | Playwright E2E: 업로드 → 검수 → 승인 (Fake provider) | 완료 (`frontend/e2e/`, `npm run e2e`, CI `e2e` 작업) |

**DoD**: 키보드 중심으로 리포트 1건을 1분 안에 검수·승인할 수 있고, E2E 테스트가 통과한다.

### M5에서 정한 것
- **검수자 식별(로그인 전 임시)**: 상단 막대에 이름을 넣으면 모든 요청에 `X-Reviewer`(URL 인코딩) 헤더로 보낸다. 서버는 이름마다 User를 하나 만든다(`reviewer-…@local`, 역할 publisher). 로그인(M0.7)을 만들면 이 의존성(`api/deps.current_reviewer`)만 바꾼다. 지금은 누구나 승인할 수 있다
- **편집 잠금**: 편집기를 열면 잠금(2분), 30초마다 갱신, 닫으면 해제. 다른 검수자는 423(읽기 전용으로 열림). 잠금 요청은 클립마다 순서대로 보낸다(개발 모드의 이중 마운트에서 해제가 다음 잠금 뒤에 도착하던 문제)
- **구간 수정**: 끝점 단위로 `start_snap`/`end_snap`이 `manual`이 된다. 구간이 바뀌면 썸네일 후보를 새 구간에서 다시 만들고(`clips.prepare_after_range`, media 큐), 메타데이터가 없던 클립(정렬 실패·미검출)은 메타데이터도 만든다. 검수자가 직접 고른 프레임·올린 이미지는 후보를 다시 만들어도 남고 선택도 유지된다. 1초 미만·녹화본 밖은 거부. 드래그는 장면 컷 ±0.25초에 붙는다(시작 = 컷 + 1프레임, 끝 = 컷 − ½프레임, Alt = 붙이지 않음)
- **승인 조건**(spec 5.8): 구간 있음, 메타데이터 있음, 제목 1~100자, 썸네일 선택됨, 예약 시각이 미래, 경고(클립 플래그 + 메타데이터 경고)가 있으면 "경고 확인" 체크. 승인·반려된 클립은 "다시 열기" 전에는 고칠 수 없다. 승인 뒤 다음 검수 전 클립으로 자동 이동. 최종 렌더(M6)는 승인에서 시작하도록 붙인다
- **재실행 보호**: 검수한 클립(수정·승인·반려)이 있으면 단계 재시도와 큐시트 다시 가져오기가 409(`reviewed_clips`)로 멈추고, 화면에서 확인을 받은 뒤 `force=true`로 진행한다(진행하면 클립·메타·썸네일을 새로 만든다)
- **예약**: 화면은 KST로 입력, 서버는 UTC로 저장. 순차 예약은 큐시트 순서대로 첫 시각부터 N분 간격, 공개 범위는 예약 후 공개(public)
- **작업 목록 집계**: `clip_counts`(전체 = 업로드 대상, 검출 = 구간 있음, 승인, 반려)
- **Fake 정렬 모델**: `ALIGN_MODEL=fake:silent`는 아무 소리도 듣지 못해 모든 아이템이 미검출이 된다(E2E, PyTorch 없는 개발 환경)
- **E2E**: Playwright가 백엔드(SQLite, Celery 즉시 실행, Fake 큐시트·정렬·LLM, `e2e/serve-backend.sh`)와 Vite를 띄우고, 합성 녹화본 업로드 → 클립 구간 지정(Shift+→, I, O) → 제목 수정(Ctrl+S) → 경고 확인 → 승인(Ctrl+Enter)과 변경 기록을 확인한다. `@playwright/test`는 세션에 설치된 브라우저와 같은 1.63.0으로 고정

---

## M6. 최종 렌더 (파이프라인 6-b) — 완료 (2026-11-21). HD 원본으로 렌더 시간 확인이 남음
> **상태 (2026-11-21)**: 6.1~6.4 구현. 프레임 번호를 밝기로 새긴 합성 영상으로 첫·마지막 프레임이 검수 화면의 프레임과 같음을 테스트로 확인(DoD). 11-16 녹화본(320×240 사본)의 116초 클립: 14초에 렌더, 길이 116.000초(구간 115.932초), -14.06 LUFS, 첫 프레임이 원본의 지정 시각 프레임과 일치(평균 밝기 차 1.0, 컷 이전 프레임은 77.9). E2E가 승인 → 렌더 완료 → 재생·다운로드까지 확인한다.

| # | Task | 상태 |
|---|---|---|
| 6.1 | 원본에서 정확한 컷 + 재인코딩 + loudnorm 2-pass + 페이드 옵션 | 완료 (`app/render/render.py`, `app/media/seek.py`) |
| 6.2 | 렌더 검증(길이 ±0.1초, 오디오 존재), 재시도 | 완료 (FFmpeg 오류는 1회 자동 재시도, 그 뒤 `render_error` + "다시 렌더") |
| 6.3 | 승인 시 렌더 자동 시작, 승인 후 경계 변경 시 렌더 무효화·재렌더 | 완료 (승인·일괄 승인 → `clips.render`(media 큐). 경계를 바꾸려면 "다시 열기" → 렌더 파일 삭제, 다시 승인하면 새로 렌더) |
| 6.4 | 렌더 결과 미리보기·다운로드 (수동 게시용 대체 경로) | 완료 (편집기 "렌더 결과": 재생, 영상·썸네일 내려받기, 다시 렌더. `/media/clips/{id}/final.mp4?download=1`) |

**DoD**: 승인된 클립이 원본 화질로 렌더되고, 시작 프레임이 검수 화면에서 지정한 프레임과 같다는 것이 테스트로 검증된다.

### M6에서 정한 것
- **프레임 정확 자르기 = 거친 입력 탐색 + 타임스탬프로 고르기** (`media/seek.py`): 입력 쪽 `-ss`는 MPEG-TS(타임스탬프가 0에서 시작하지 않는 파일)에서 6프레임 늦게 떨어졌다(MP4는 정확). 그래서 3초 앞까지만 입력 쪽으로 탐색하고, 원본 타임스탬프(`-copyts`)로 `trim`/`atrim`해 프레임을 고른다. 첫 프레임 = 시작 시각에 화면에 보이는 프레임(pts ≤ 시작 < pts + 1프레임, 검수 플레이어와 같다), 끝 = pts < 끝인 프레임까지. **썸네일 프레임 추출도 같은 함수**를 쓴다(골라 둔 썸네일 프레임 = 렌더 첫 프레임, 테스트로 묶음). 비디오가 오디오보다 늦게 시작하는 파일도 파일 시작 기준으로 맞다
- **렌더 규격**: H.264 High CRF 18 preset fast, 원본 해상도(최대 1080p), 인터레이스면 yadif, 원본 프레임 레이트, AAC 192k 48kHz 스테레오, `+faststart`, 메타데이터 제거. 음량 loudnorm 2-pass(1차 측정 → 2차 `measured_*` + `linear=true`), -14 LUFS, TP -1, LRA 11. 무음 클립은 정규화를 건너뛴다. 오디오 트랙은 프로필 `audio_track`, 없으면 트랙 하나 또는 전체 믹스
- **페이드 끔 (2026-11-21 사용자 결정)**: 첫 프레임이 검수에서 지정한 프레임 그대로 나가도록 페이드 인·아웃을 쓰지 않는다. 설정(`RENDER_FADE_IN`, `RENDER_FADE_OUT`, 초)으로 켤 수는 있다
- **상태**: 승인 → `APPROVED` → 워커가 시작하면 `RENDERING` → `RENDERED`(+ `render_path`, `rendered_at`). 실패하면 `APPROVED` + `render_error`. 렌더 중 다시 열기·구간 변경이 있었으면 결과를 버린다. 렌더된 클립도 "검수한 클립"(재실행 보호, 작업 목록의 승인 수에 포함)
- **승인 뒤에도 발행 설정(공개 범위, 예약, 채널)은 바꿀 수 있다**(렌더와 무관). 제목·설명 등 나머지는 다시 열어야 한다. 순차 예약은 렌더된 클립에도 된다
- 편집기는 승인·렌더 중인 클립을 2초마다 다시 읽어 렌더 완료를 보여 준다
- 성능: 320×240 사본 116초 클립 14초. HD(1080i) 원본은 디인터레이스·인코딩으로 더 걸린다 → HD 원본으로 확인할 것(spec 9: 클립 1건 ≤ 1분)

### M6 시작 가이드 (새 세션용, 2026-11-21 작성 — M6 구현 전 기록)

**먼저 읽을 것**: spec 4.2(클립 상태), 5.6 6-b(렌더 규격), 5.8 승인 조건, 6.2(저장 경로 `clips/{clip_id}/final.mp4`), 9(성능: 클립 1건 렌더 ≤ 1분). CLAUDE.md의 "PoC에서 배운 주의점"(프레임 정확 자르기, 파일 시작 기준 시각). 이 파일의 "M5에서 정한 것"

**지금 상태 (M0~M5 완료, `main`)**
- 검수·승인까지 된다. 승인은 `backend/app/review/service.py`의 `approve()`(끝에 `# The final render (M6) starts from here.`), API는 `POST /api/clips/{id}/approve`와 일괄 승인 `POST /api/clips/bulk`(`api/review.py`). 승인 뒤에는 고칠 수 없고 "다시 열기"(`reopen()`, `APPROVED/REJECTED → DRAFT`)로만 바뀐다 → **6.3의 "승인 후 경계 변경"은 다시 열기 = 렌더 무효화**로 처리하면 된다
- 클립 모델: `models/clip.py`의 `Clip`. `ClipStatus`에 `RENDERING`, `RENDERED`가 이미 있고 `render_path` 칼럼도 있다(비어 있음). 구간은 `start_sec`/`end_sec`(파일 시작 기준 초, 소수 3자리). 프레임 레이트는 `Job.fps`, 원본 정보는 `jobs/{id}/source/probe.json`(`ProbeResult`: `video_start`, `interlaced`, `audio[]`, `fps_rational`)
- 원본·오디오 트랙: `pipeline/handlers.py`의 `load_source()`가 원본 경로·probe·프로그램 오디오 트랙(`ProgramProfile.audio_track`, None = 기본/전체)을 준다. 프록시는 `media/proxy.py`(`-map 0:a:{order}`), 정렬 오디오는 `media/analysis.py`(트랙이 여럿이면 amix) 참고. 디인터레이스 필터는 `analysis.DEINTERLACE`
- FFmpeg 실행: `media/ffmpeg.py`의 `run_ffmpeg(args, duration=, on_progress=, timeout=)`(stderr 반환, loudnorm 1-pass 측정값 JSON을 stderr에서 읽으면 된다), `probe()`
- 저장 경로: `storage.py`의 `JobFiles.clip_dir(clip_id)`가 있다 → `final.mp4`를 여기에 추가. 브라우저 다운로드는 `api/media.py`에 라우트 추가(썸네일 `/media/clips/{id}/thumbnail.jpg` 패턴)
- Celery: 큐 `media`에서 돈다. 클립 단위 태스크 예시는 `pipeline/tasks.py`의 `prepare_clip_after_range`(media 큐), `regenerate_clip_metadata`. 작업 변경 알림은 `publish_job_changed(job_id)`
- 화면: 클립 편집기 `frontend/src/pages/ClipEditorPage.tsx`(승인 영역 `approve-box`), 작업 상세 클립 목록 `JobDetailPage.tsx`, 상태 이름 `labels.ts`의 `CLIP_STATUS_LABEL`(RENDERING/RENDERED 추가 필요). 일괄 승인은 `BulkBar`
- 재실행 보호(`review.reviewed_clips`, 검수한 클립이 있으면 단계 재시도 409): 렌더된 클립도 "검수한 클립"이다(상태가 바뀌니 `REVIEWED` 집합 확인)
- 테스트: `tests/test_review.py`의 `reviewed` 픽스처(합성 원본 영상 + 클립 3개, `tests/test_thumbnails.job_with_source`)를 그대로 쓸 수 있다. E2E는 `frontend/e2e/review.spec.ts`(승인까지) → 렌더 완료·다운로드 확인을 덧붙이면 된다. Celery는 테스트에서 즉시 실행된다

**만들 것 (제안)**
- `backend/app/render/`: 렌더 명령(원본 `-ss START -i SRC -t DUR`이 아니라 **출력 쪽에서 길이**: PoC 규칙대로 `-ss`는 입력 쪽 탐색 + `-t`로 길이, `-to` 금지), H.264 High CRF 18, 최대 1080p(설정값), AAC 192k 48kHz, `+faststart`, 인터레이스면 디인터레이스, loudnorm 2-pass(-14 LUFS, 1차 측정 → 2차 `measured_*` 적용), 페이드 인 0.2초/아웃 0.3초(옵션, 기본값은 정할 것)
- 검증(6.2): ffprobe 길이 ±0.1초, 오디오 스트림 존재, 실패 시 재시도(RetryableStepError 같은 일시 오류만), 최종 실패면 클립에 오류 기록(`Clip.render_error` 같은 칼럼 → 마이그레이션 0010)
- 상태 흐름: 승인 → 렌더 태스크 큐(`RENDERING`) → 성공 `RENDERED` + `render_path` / 실패 시 상태 표시와 재시도 API(`POST /api/clips/{id}/render`). 다시 열기 → `final.mp4` 삭제·`render_path` 비움
- 6.4: 편집기·목록에 렌더 상태, 렌더 결과 재생(`<video src=/media/clips/{id}/final.mp4>`)과 다운로드(수동 게시용), 썸네일 다운로드
- 테스트(DoD): 합성 영상에서 **렌더 첫 프레임 = 검수 화면에서 지정한 시각의 프레임**(색이 다른 장면 경계에 구간을 두고 첫·마지막 프레임 색 확인, 프레임 번호를 그린 영상이면 더 확실: `drawtext=text='%{n}'`), 길이 ±0.1초, 오디오 있음, loudnorm 적용 후 음량 측정, 오디오가 먼저 시작하는 원본(`start_offset`, `video_start` > 0)

**주의점**
- 시각은 모두 **파일 시작 기준**이다. 입력 쪽 `-ss`는 파일 시작 시각을 더해 탐색하므로 맞지만, 비디오 스트림이 늦게 시작하는 파일(`probe.video_start > 0`)에서 오디오·비디오가 같이 잘리는지 확인한다
- 썸네일 프레임 추출(`thumbnails/candidates.extract_frame`)이 같은 탐색 방식을 쓴다. 렌더 첫 프레임과 썸네일 프레임이 같은 축인지 테스트로 묶어 두면 좋다
- 렌더는 원본 화질이라 오래 걸린다(2분 클립 1080i, CPU). media 워커 동시 실행 수(`MEDIA_CONCURRENCY`)를 고려하고 진행률을 알린다

**사용자에게 받을 것 / 정할 것**
- 페이드 인·아웃 기본값(켤지), 목표 음량(-14 LUFS 기본), 최대 해상도(1080p 기본)
- 실제 원본(HD) 녹화본으로 렌더 시간·화질 확인은 녹화본이 필요하다(축소본으로 대신해도 되는지)

---

## M7. YouTube 업로드·예약 발행 (파이프라인 9) — 완료 (2026-11-21, 테스트 채널에서 예약 공개 확인)
> **상태 (2026-11-21)**: 7.1~7.5 구현. 가짜 플랫폼(`PUBLISHER=fake`)으로 승인 → 렌더 → 업로드 → 예약 → 게시까지 테스트·E2E가 통과하고, YouTube 어댑터는 모의 API(httpx MockTransport)로 resumable 업로드·재개·토큰 갱신·오류 분류를 검증했다. 실제 테스트 채널에서 예약 업로드 → 썸네일·메타데이터 반영 → 예약 시각에 공개까지 확인했다(아래 "실제 채널 확인").

| # | Task | 상태 |
|---|---|---|
| 7.1 | `Publisher` 인터페이스 + `FakePublisher` | 완료 (`app/publish/base.py`, `fake.py`: 상태를 `storage/fake_publisher/*.json`에 둬 API·워커가 같이 본다. 예약 시각이 지나면 공개로 보인다. 실패 주입 가능) |
| 7.2 | Google OAuth 2.0 채널 연동, 토큰 암호화 저장, 갱신 | 완료 (`oauth.py`, `channels.py`, `crypto.py`. 기기 코드 방식 + 브라우저 리디렉트 방식, Fernet 암호화, 만료 60초 전 갱신, `invalid_grant` → 채널 "다시 연결 필요") |
| 7.3 | YouTube 어댑터: resumable upload(재개 URI 저장), publishAt 예약, 썸네일, 카테고리·언어 | 완료 (`youtube.py`, httpx 직접 호출 — Google 클라이언트 라이브러리는 쓰지 않음) |
| 7.4 | 할당량 추적, 한도 도달 시 다음 날로 대기 | 완료 (`quota.py`: 프로젝트 전체 일일 사용량(`quota_usage`), 업로드 전에 1,650 units 예약, 모자라거나 403 quotaExceeded면 태평양 시간 자정 + 5분으로 미룸) |
| 7.5 | PublishTask 생성(승인 + 렌더 완료 → 업로드 큐), 업로드 대시보드 | 완료 (렌더 끝 → 클립 채널마다 `PublishTask` → publish 큐. 화면 `/publish`(게시 현황), `/channels`(채널 연결·프로그램 기본 채널), 편집기 업로드 칸) |

**DoD**: 테스트 채널에 비공개 예약 업로드가 성공하고, 예약 시각에 공개되며 썸네일과 메타데이터가 반영된다.

### M7에서 정한 것
- **흐름**: 승인 → 렌더(`RENDERED`) → 클립의 `channel_ids`마다 `PublishTask`(QUEUED) → publish 워커 업로드(UPLOADING) → UPLOADED → 예약이면 SCHEDULED, 아니면 PROCESSING → PUBLISHED. 실패는 FAILED, 사용자 취소는 CANCELLED(기록으로 남김). **클립 상태는 작업 중 가장 덜 진행된 것**(실패가 하나라도 있으면 `UPLOAD_FAILED`)
- **업로드 시점**: 렌더가 끝나면 자동(`PUBLISH_AUTO=true`, 승인이 사람의 확인). 채널을 고르지 않았으면 렌더까지만 하고 편집기의 "업로드" 버튼이나 작업 상세의 "렌더 완료 N건 업로드"로 올린다
- **채널 선택**: 메타데이터를 처음 만들 때 프로그램 프로필 `default_channels`로 채운다(채널 화면의 "프로그램 기본 채널"). 편집기 "업로드 채널"에서 클립마다 바꾼다. 연결된(active) 채널만 고를 수 있다
- **발행 설정 잠금**: 채널·공개 범위·예약은 업로드가 시작되기 전(승인·렌더 중·렌더 완료·업로드 실패)까지만 바꿀 수 있다. 올라간 뒤 바꾸려면 업로드를 취소(영상 삭제)한다. 원격 메타 수정(`videos.update`)은 M8. "다시 열기"는 플랫폼에 영상이 없을 때만 되고 대기 중인 작업을 취소한다
- **예약**: `privacyStatus=private` + `publishAt`(UTC). 업로드 시작 때 예약 시각이 2분 안이거나 지났으면 올리지 않고 실패(invalid)로 둔다 → 시각을 바꾸고 다시 시도. 예약 시각이 15분 지나도 비공개면 실패로 알린다(미인증 API 프로젝트는 비공개로 고정됨, 아래)
- **재시도(M7 범위)**: 네트워크·5xx·429·rateLimit → 1, 2, 4, 8, 16분 뒤(최대 `PUBLISH_MAX_ATTEMPTS`=5), 같은 resumable 세션으로 이어 올린다(청크 실패는 프로세스 안에서 3번 먼저 이어 본다). 401 → 토큰 갱신 후 한 번 더, 그래도 실패면 채널 "다시 연결 필요". 400·403(forbidden 등) → 재시도 없이 실패. 할당량 → 다음 날. 썸네일만 실패하면 영상은 두고 썸네일만 다시(버튼). 수동 "다시 시도"는 횟수를 처음부터 센다
- **Celery beat**(1분, `publish.sweep`): 재시도 시각이 된 작업 시작, 10분 넘게 진척 없는 업로드 재시작, 상태 확인(처리 중 2분마다, 예약 시각 직후와 그 뒤 5분마다, 처리 전인 예약 영상 10분마다). Compose에 `beat` 서비스 추가. 점진적 폴링·알림은 M8
- **설명**: 검수한 설명 끝에 해시태그를 붙여 올린다(이미 있으면 그대로). 제목·설명의 `<` `>`는 전각으로 바꾼다(YouTube 거부 문자). 태그는 YouTube 500자 계산(쉼표, 공백 있는 태그는 따옴표)으로 자른다. `categoryId=25`, `defaultLanguage=defaultAudioLanguage=ko`, `selfDeclaredMadeForKids=false`
- **할당량은 Google Cloud 프로젝트 단위**라 채널마다가 아니라 플랫폼 전체로 센다(spec 7의 `Channel.quota_used_today` 대신 `quota_usage` 표). 비용은 설정값(`YOUTUBE_QUOTA_INSERT`=1600, `_THUMBNAIL`=50)
- **OAuth**: 범위는 `https://www.googleapis.com/auth/youtube` 하나(업로드·썸네일·상태·삭제). 클라이언트 시크릿은 토큰 엔드포인트에 HTTP Basic으로 보낸다 → 프록시가 넣어 줄 수도 있다(`GOOGLE_CLIENT_SECRET=proxy-injected`). 리디렉트 방식은 https(또는 localhost) 주소가 필요해서, 사내 http 서버에서는 **기기 코드 방식**(클라이언트 유형 "TV 및 입력 제한 기기")을 쓴다. 토큰은 `TOKEN_ENCRYPTION_KEY`(Fernet) 없이는 저장하지 않는다
- **권한**: 로그인(M0.7)이 없어 지금은 검수자 이름만 있으면 누구나 채널을 연결·해제한다(spec은 관리자). 업로드 취소(영상 삭제)는 확인 창을 거친다
- **SQLite에서 예약 시각이 9시간 어긋나던 문제(M5부터)**: `+09:00` 시각을 그대로 저장해 SQLite가 시간대를 버렸다 → 저장 전에 UTC로 바꾼다(PostgreSQL은 원래 맞았다)

### 실제 채널 확인 (2026-11-21, 테스트 채널 `UCOj1kGSkQmNtX_zhMJgmu3A`)
- 기기 코드 방식으로 채널 연결 성공. 처음 받은 클라이언트는 "TV 및 입력 제한 기기" 유형이 아니어서 `Invalid client type` → 새로 만든 TV 유형 클라이언트로 연결
- 합성 녹화본(1280×720, 20초) 클립 8초: 업로드(resumable) → `privacyStatus=private` + `publishAt` 예약 → 맞춤 썸네일 반영(maxres) → 제목·설명(해시태그 포함)·태그·`categoryId=25`·`defaultLanguage=ko` 반영을 `videos.list`로 확인
- 예약 시각(04:54 UTC) 뒤 첫 상태 확인(04:55)에서 `processed` + `public` → 작업·클립 `PUBLISHED`. **DoD 충족**. 이 프로젝트에서는 미인증 프로젝트의 비공개 고정이 일어나지 않았다(운영 전 감사·할당량 증설 신청은 여전히 필요)

### 실제 방송으로 업로드 한도 확인 (2026-11-21, 11-16 뉴스데스크 320×240 축소본)
- 실제 MARS-L 큐시트 + 실제 정렬 모델(CPU, 322초): 업로드 대상 25건 중 11건 구간 검출(리포트 5, 단신 6). 녹화본이 20분에서 끝나 뒤쪽 단신 13건은 미검출, 2건은 신뢰도 낮음(수동 지정 대상)
- 11건 일괄 승인 → 렌더 → 비공개 업로드. 앱의 할당량 계산(1,650 units/건)을 끄고 Google이 막을 때까지 시도
- **결과: 하루 10번째 업로드까지 성공(앞선 테스트 1건 포함), 11번째에서 `400 uploadLimitExceeded`**("The user has exceeded the number of videos they may upload"). 이것은 프로젝트 units 할당량(`403 quotaExceeded`)이 아니라 **채널의 하루 업로드 개수 한도**다. units 기준(1,600/건)이면 6번째에서 막혔어야 하므로 videos.insert의 실제 비용은 문서의 1,600보다 작다 → Google Cloud 콘솔의 할당량 페이지에서 실제 사용량을 확인하고 `YOUTUBE_QUOTA_INSERT`를 맞출 것
- 9건 모두 처리 완료(processed), 썸네일 9/9 반영. 막힌 2건은 할당량 대기(태평양 시간 자정 + 5분)로 미뤄짐
- 고친 것: `uploadLimitExceeded`는 채널 한도이므로 프로젝트 할당량 카운터를 소진 처리하지 않는다(다른 채널은 계속 올릴 수 있게)
- 운영 영향: 채널당 하루 업로드 개수에 한도가 있다(이 채널은 10건). 뉴스데스크 + 뉴스투데이 리포트·단신을 한 채널에 올리면 넘을 수 있다 → 채널 인증 상태·한도 확인, 필요하면 채널 분리 검토

### M7 뒤에 바뀐 것
- **유튜브 설명 = `[목포MBC 뉴스]` + 큐시트 원고 그대로 (2026-11-21 사용자 결정, 사용자가 준 예시와 같은 형식)**. 이전에는 앵커 리드 + 꼬리말(취재 기자·방송일)이었다. 원고는 저장한 큐시트 응답(`Rundown.raw`)의 `articleContent`에서 줄 앞뒤 공백, 연속 빈 줄, **표시 줄(◀ 앵 커 ▶, ◀ 리포트 ▶, ◀ SYNC ▶ …) 바로 다음의 빈 줄**만 정리한다(`metadata/script.tidy_script`, 2026-11-21 사용자 요청. ◀ END ▶ 뒤의 빈 줄은 앵커·리포트 구분이라 남긴다). 표시와 편집기 줄바꿈(단어 중간이라도)은 그대로 둔다. 꼬리말은 프로그램 프로필 `description_footer`를 정했을 때만 붙인다. **해시태그는 업로드 때 설명 끝에 붙인다**(빈 줄 뒤 `#a #b`, 이미 있으면 생략. 2026-11-21 사용자 요청 — 바로 전 결정을 되돌림). YouTube 5,000바이트를 넘으면 원고를 줄 단위로 자른다(…). 이미 만든 메타데이터는 "다시 만들기"를 해야 바뀐다

### 실제 채널 확인 절차 (운영 채널을 연결할 때도 같다)
1. Google Cloud 프로젝트에서 **YouTube Data API v3 사용 설정**, OAuth 동의 화면(외부, 테스트 모드면 채널 소유 Google 계정을 테스트 사용자로 추가), OAuth 클라이언트 **"TV 및 입력 제한 기기"** 생성 → 클라이언트 ID·시크릿
2. 서버 `.env`: `PUBLISHER=youtube`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`. 클라우드 세션에서는 시크릿을 `request_credential`(호스트 `oauth2.googleapis.com`, 사용자 이름 = 클라이언트 ID, 비밀번호 = 시크릿, Basic)로 받고 `GOOGLE_CLIENT_SECRET=proxy-injected`. 네트워크에 `oauth2.googleapis.com`, `www.googleapis.com` 허용 필요
3. 채널 화면 → "Google 계정으로 연결" → google.com/device에 코드 입력 → **테스트 채널(브랜드 계정)** 선택 → 허용
4. 클립 승인(채널 선택, 비공개 대신 공개 + 예약 몇 분 뒤) → YouTube 스튜디오에서 예약·썸네일·제목·설명 확인 → 예약 시각 뒤 공개 확인
- **주의**: Google 문서상 2020-07-28 이후 만든 **미인증 API 프로젝트로 올린 영상은 비공개로 잠길 수 있다**(YouTube API 감사 통과 전). 테스트 프로젝트에서는 예약 공개가 됐지만(2026-11-21), 운영 프로젝트도 같은지 확인한다. 감사 신청과 할당량 증설 신청은 같은 양식(YouTube API Services Audit and Quota Extension Form)으로 한다
- 맞춤 썸네일은 채널이 전화번호 인증을 해야 된다(안 됐으면 썸네일만 실패로 표시)

### M7 시작 가이드 (2026-11-21 작성 — M7 구현 전 기록, 지금 상태는 위 절들을 볼 것)

**먼저 읽을 것**: spec 4.2(클립 상태: `RENDERED → UPLOADING → UPLOADED → PROCESSING → SCHEDULED → PUBLISHED`, `UPLOAD_FAILED`), 5.9(Publisher 인터페이스, YouTube 규격, 할당량), 5.10(재시도 정책 — 본격 구현은 M8), 7(`Channel`, `PublishTask`), 8(`/api/publish-tasks`, `/api/channels`, OAuth 콜백). 이 파일의 "M5에서 정한 것", "M6에서 정한 것"

**지금 상태 (M0~M6 완료, `main`)**
- 업로드 직전까지 된다: 검수 승인 → 원본 화질 렌더(`RENDERED`). 렌더 파일은 `Clip.render_path`(`jobs/{id}/clips/{clip_id}/final.mp4`, `JobFiles.final()`), 썸네일은 `JobFiles.thumbnail(clip_id)`(`thumbnail.jpg`, 1280×720 JPG ≤ 2MB, 리포트는 문구 오버레이, 단신은 프레임 그대로)
- 렌더 태스크: `backend/app/pipeline/tasks.py`의 `render_clip`(`clips.render`, media 큐)이 끝에서 `RENDERED`로 바꾼다 → **여기서 업로드 작업(PublishTask)을 만들어 publish 큐에 넣으면 된다**(7.5). 클립 단위 태스크·`publish_job_changed(job_id)` 패턴은 같은 파일 참고
- 발행 설정(검수 화면에서 입력): `ClipMetadata.title`(이미 `원고 제목[목포MBC 뉴스데스크]` 형식, 100자 이내 검증), `description`, `tags`(500자 이내로 잘림), `hashtags`, `privacy`(private/unlisted/public = 공개 후 상태), `publish_at`(UTC 저장, 화면은 KST 입력; 있으면 예약 = private + publishAt), `channel_ids`(빈 목록 — 채널이 생기면 채움). 프로그램 프로필 `default_channels`(빈 목록) 칼럼이 이미 있다. 발행 설정은 승인·렌더 뒤에도 바꿀 수 있다(`review.PUBLISH_FIELDS`)
- 클립 상태 집합: `review/service.py`의 `EDITABLE`, `REVIEWED`, `APPROVED_STATES`. 업로드 상태(`UPLOADING` 이후)를 추가하면 **"다시 열기"(`reopen`)를 막고**(이미 올라간 영상), 재실행 보호(`reviewed_clips`)·작업 목록 집계(`api/jobs.clip_counts`)에도 넣는다
- 외부 연동 패턴: 인터페이스 + Fake + 설정으로 선택. 예시 `newsroom/provider.py`·`fake.py`·`factory.py`(`set_provider()`로 테스트에서 바꿔 끼움), `metadata/llm.py`·`factory.py`. → `publish/` 패키지에 `Publisher`, `FakePublisher`, `YouTubePublisher`, `PUBLISHER=fake|youtube`
- 비밀값: MARS-L 키처럼 환경의 네트워크 비밀(프록시 주입)이 가능한 것은 헤더 방식뿐이다. Google OAuth는 클라이언트 ID/시크릿으로 토큰을 교환하고 refresh token을 DB에 **암호화** 저장해야 한다(spec 7 `oauth_token(encrypted)`) → 암호화 키는 설정(`TOKEN_ENCRYPTION_KEY`, Fernet 등). 시크릿은 커밋하지 않고 `.env`로
- 화면: 클립 편집기 `frontend/src/pages/ClipEditorPage.tsx`의 메타데이터 폼(공개 설정·예약 칸 있음, **채널 선택 칸이 없음** — M5에서 미룬 것), `RenderStatus`(렌더 결과) 아래에 업로드 상태를 붙이면 된다. 작업 상세 `JobDetailPage.tsx`의 `BulkBar`(일괄 승인·순차 예약). 업로드 대시보드는 새 페이지(`/publish`)
- 테스트: `tests/test_review.py`의 `reviewed` 픽스처(합성 원본 + 클립 3개)에서 승인하면 Celery 즉시 실행으로 바로 `RENDERED`가 된다 → 그 뒤 업로드를 Fake로 검증. E2E(`frontend/e2e/review.spec.ts`)는 렌더 완료까지 → Fake 업로드 완료를 덧붙인다

**만들 것 (제안)**
- 모델(마이그레이션 0011): `Channel`(platform, name, remote_channel_id, 암호화한 토큰, 일일 사용량·날짜), `PublishTask`(clip_id, channel_id, status, remote_id, remote_url, attempts, next_retry_at, last_error, resumable_uri, published_at, scheduled_at). 클립 하나에 채널마다 하나
- `Publisher` 인터페이스(spec 5.9: upload, set_thumbnail, get_status, update, delete) + `FakePublisher`(가짜 remote_id, 예약 시각이 지나면 PUBLISHED로 보이게)
- YouTube 어댑터: OAuth 2.0(관리자 연결 → `/api/channels/oauth/callback`, 토큰 암호화·갱신), `videos.insert` resumable(청크 8MB 배수, 세션 URI를 `resumable_uri`에 저장해 재개), `privacyStatus=private` + `publishAt`(예약, UTC), `categoryId=25`, `defaultLanguage=ko`, `thumbnails.set`. HTTP는 `httpx`(이미 의존성) 또는 Google 클라이언트 라이브러리 — 의존성 추가 여부를 정할 것
- 할당량(7.4): `videos.insert` 1,600 units + `thumbnails.set` 50 units, 기본 하루 10,000 → 하루 약 6건. 채널별 사용량을 태평양 시간 자정 기준으로 세고, 넘으면 다음 날로 미루고 경고
- 흐름(7.5): `RENDERED` + 채널 있음 → PublishTask(QUEUED) → publish 큐 업로드 → `UPLOADED`/`SCHEDULED` → (M8) 상태 폴링 → `PUBLISHED`. 업로드 대시보드(`GET /api/publish-tasks`), 채널 관리 화면
- 재시도는 M7에서는 단순하게(일시 오류 몇 번), 오류 유형별 정책·폴링·장애 주입 테스트는 M8

**사용자에게 받을 것 / 정할 것**
- **YouTube 테스트 채널 + Google Cloud OAuth 클라이언트**(실제 업로드 확인, DoD). 없으면 Fake로 끝까지 만들고 실제 확인만 남긴다. 클라우드 세션에서 Google API에 접근하려면 환경 네트워크에 `oauth2.googleapis.com`, `www.googleapis.com`(업로드는 `youtube.googleapis.com`/`www.googleapis.com/upload`)이 허용돼야 한다 — 막히면 `get_environment`로 확인 후 요청
- **YouTube API 할당량 증설 신청**(승인에 수 주) — 하루 업로드 편수(뉴스데스크 + 뉴스투데이의 리포트·단신 수)로 규모를 정해야 한다(spec 16-4)
- 채널 구성: 목포MBC 채널 하나에 프로그램별 재생목록을 쓸지, 프로그램마다 채널이 다른지. 기본 공개 설정(지금 기본 private)
- 업로드는 승인 즉시인지, 검수자가 "업로드" 버튼을 누를 때인지(지금 계획: 렌더 완료 즉시 자동)

**기존 남은 일 (M7과 별개)**
- 로그인(M0.7 보류): 지금은 `X-Reviewer` 헤더로 검수자를 구분하고 누구나 승인한다. 채널 연동(관리자 전용)과 함께 로그인을 만들지 정할 것
- M3.8 원래 골든셋 4편 재현, M4 실제 Claude 호출(키 필요), HD 원본으로 썸네일 판정·렌더 시간 확인, 리포트 썸네일 템플릿 PNG 교체(사용자 제공 예정)

---

## M8. 상태 확인·재시도·알림 (파이프라인 10) — 완료 (2026-11-21)
> **상태 (2026-11-21)**: 8.1~8.3, 8.5 구현. 외부 알림(8.4)은 만들지 않기로 했다(사용자 결정). 장애 시나리오별 상태 전이를 가짜 플랫폼으로 테스트하고(`tests/test_publish_m8.py`), 업로드 뒤 수정의 자동 반영은 실제 테스트 채널의 비공개 영상에서도 확인했다(아래). 백엔드 테스트 257개, E2E(업로드 뒤 제목 수정 → 반영) 통과.

| # | Task | 상태 |
|---|---|---|
| 8.1 | 상태 폴링(점진적 간격), processing/rejection 반영 | 완료 (작업별 `next_check_at`: 처리 중 1·2·4·8분 뒤, 그다음 10분마다. 처리된 예약 영상은 예약 시각 1분 뒤. 게시 뒤 10분·1시간·6시간·24시간에 다시 확인해 거부·삭제를 잡고 그 뒤로는 확인하지 않음) |
| 8.2 | 오류 유형별 재시도 정책 + 지수 백오프 | 완료 (M7 정책 + 썸네일 일시 오류 자동 재시도 2·10·30분. 권한 거절(forbidden)은 재시도하지 않음) |
| 8.3 | 수동 재시도/취소/원격 메타 수정 | 완료 (**업로드 뒤 수정은 자동 반영**: 제목·설명·태그·해시태그·공개 범위·예약·썸네일 → `videos.update`/`thumbnails.set`. 반영 실패는 표시 + "다시 반영") |
| 8.4 | ~~웹 알림 + Slack/메일 Webhook~~ | 하지 않음 (2026-11-21 사용자 결정: 외부 알림 없음. 게시 현황·편집기에 상태·사유·반영 상태 표시) |
| 8.5 | 장애 주입 테스트(네트워크 끊김, 401, quotaExceeded, 5xx) | 완료 (네트워크, 5xx·5회 연속, 세션 만료, 401, quotaExceeded, uploadLimitExceeded, 잘못된 메타데이터, 게시 뒤 거부 → 새 영상으로 재업로드, 멈춘 워커, 반영 중 수정, 업로드 중 수정) |

**DoD**: 장애 시나리오별 상태 전이와 재시도가 테스트로 검증된다.

### M8에서 정한 것
- **업로드 뒤 수정 (2026-11-21 사용자 결정: 자동 반영)**: 업로드된(또는 올라가는 중인) 클립은 제목·설명·태그·해시태그·썸네일 문구·썸네일 프레임·공개 범위·예약을 고칠 수 있다. 저장하면 그 클립의 모든 업로드 작업에 반영 대기(`sync_status=pending`)를 걸고 publish 큐에서 `videos.update`(썸네일이 바뀌었으면 `thumbnails.set`도)를 부른다. **채널과 구간은 그대로 잠금**(업로드를 취소해야 바꿈). **이미 게시된 영상은 예약 시각을 바꿀 수 없다**(공개 범위는 바꿀 수 있음). 새 예약 시각은 2분 뒤 이후여야 한다
- **반영 버전**: 수정마다 `sync_version`이 오르고, 반영이 끝났을 때 그사이 또 수정됐으면 다시 반영한다. 업로드 중에 고친 것은 업로드가 끝난 뒤 반영한다. 아직 올라가지 않은 대기 작업은 최신 메타데이터로 올라간다
- **반영 실패**: 네트워크·5xx는 1, 2, 4, 8, 16분 뒤 다시(최대 5회), 할당량은 다음 날, 401은 채널 "다시 연결 필요", 거절(400 등)은 실패로 표시하고 "다시 반영" 버튼
- **`videos.update`는 영상을 먼저 읽고 바꾸는 항목만 합쳐 보낸다**: update는 보낸 part의 모든 속성을 바꾸므로, status를 그대로 보냈더니 실제 영상의 `embeddable`·`publicStatsViewable`이 false로 꺼졌다(2026-11-21 실제 채널에서 발견 → 고침, 그 영상은 되돌림). 비용은 조회 1 + 수정 50 = 51 units(`YOUTUBE_QUOTA_UPDATE`)
- **상태 확인**: 1분 sweep이 `next_check_at`이 된 작업만 확인한다(M8 이전 작업은 `next_check_at`이 없어 바로 확인). 상태가 바뀌면 간격을 처음부터 센다. 확인도 할당량 1 unit, 할당량 오류면 초기화 뒤로
- **썸네일 할당량**: 업로드 때 첫 썸네일은 업로드 예약(1,650)에 포함, 재시도·수정 때만 50 units를 더 센다
- **알림 없음 (2026-11-21 사용자 결정)**: Slack·메일 알림은 만들지 않는다. 화면(게시 현황, 편집기 업로드 칸)에 상태·사유·다음 시도 시각·반영 상태를 보여 준다

### 실제 채널에서 반영 확인 (2026-11-21)
- 11-16 단신(비공개, `gJT4ax39Zws`) 제목을 앱에서 고침 → `videos.update` 성공, 제목 반영 확인. 이때 `embeddable`·`publicStatsViewable`이 false가 되는 문제를 발견해 "읽고 합쳐 보내기"로 고쳤고, 두 설정을 되돌린 뒤 다시 제목을 원래대로 고쳐 반영(설정 유지, 맞춤 썸네일 유지)까지 확인

### M8 시작 가이드 (2026-11-21 작성 — M8 구현 전 기록, 지금 상태는 위 절들을 볼 것)

**먼저 읽을 것**: spec 5.9(업로드 흐름, YouTube, 할당량·채널 한도), 5.10(상태 대응, 재시도 표 — M7에서 된 것과 M8 몫이 표시돼 있다), 7(`PublishTask`), 16-7·16-8(남은 확인 사항). 이 파일의 "M7에서 정한 것", "실제 방송으로 업로드 한도 확인", "M7 뒤에 바뀐 것"

**지금 상태 (M0~M7, 브랜치 `claude/laughing-hopper-8udxh2` — `main`에 아직 합치지 않음)**
- 업로드 흐름 전체가 `backend/app/publish/service.py`에 있다: `enqueue`(작업 생성) → `run_upload`(`_claim`에서 할당량 예약 → 업로드 → `set_thumbnail`) → 실패는 `_upload_failed`(오류 종류별 처리) → `apply_remote`(원격 상태 → 작업 상태) → `sync_clip`(클립 상태). 상수 `BACKOFF_MINUTES`, `QUOTA_GRACE`, `PUBLISH_LATE`, `STALE_UPLOAD`
- 주기 작업: `sweep()`(Celery beat 1분, `publish.sweep`)이 재시도 시각이 된 작업, 멈춘 업로드, 상태 확인 대상(`_check_due`, **고정 간격**)을 골라 `publish.upload`/`publish.check`(publish 큐)로 보낸다. 상태 확인은 `check_status()` — `PUBLISHED`가 되면 더 보지 않는다
- 수동 조작: `retry()`(실패 → 다시, 원격 영상이 있으면 상태만 다시 확인, 썸네일만 실패면 썸네일만), `cancel()`(원격 영상 삭제), API `api/publish.py`(`/api/publish-tasks/{id}/retry|cancel|check`, `/api/clips/{id}/publish`, `/api/publish/summary`). `Publisher.update()`는 YouTube·Fake 모두 구현돼 있지만 **아무 데서도 부르지 않는다**(8.3 원격 수정의 출발점)
- 발행 설정 잠금: `review/service.py`의 `PUBLISH_EDITABLE`, `update_metadata()` — 업로드가 시작되면 채널·공개 범위·예약을 바꿀 수 없다(409). 승인 뒤 제목·설명 등은 "다시 열기"로만 고칠 수 있는데, 다시 열기는 올라간 영상이 있으면 막힌다 → 8.3에서 풀어야 한다. 예약 시각이 지난(게시된) 영상의 `publishAt`은 바꿀 수 없다
- 장애 주입: `FakePublisher(fail={"upload": [PublishError(...)], "set_thumbnail": [...]})`, `factory.set_publisher(lambda ch: ...)`로 바꿔 끼운다. YouTube 프로토콜은 `tests/test_youtube.py`의 `FakeYouTube`(httpx MockTransport: 308 Range, 세션 만료, 401, 오류 사유)
- 알림 기반: `core/events.py`의 Redis pub/sub → SSE(`/api/events`)는 `{"type": "job", "id": …}`만 보낸다. 화면은 `frontend/src/api/events.ts`가 받아 쿼리를 다시 읽는다. 업로드 이벤트 종류(실패, 재연결 필요, 할당량 소진)를 추가하면 웹 알림의 출발점이 된다. 채널 상태는 `Channel.status`(`reauth_required`)와 `last_error`
- 화면: 게시 현황 `frontend/src/pages/PublishPage.tsx`(`TaskRow`, `TaskCard` — 편집기에서도 씀), 채널 `ChannelsPage.tsx`, 편집기 업로드 칸 `ClipEditorPage.tsx`의 `PublishStatus`
- 시각: DB는 UTC로 저장한다(SQLite는 시간대를 버리므로 `_aware()`로 UTC로 본다). 할당량 날짜는 태평양 시간(`publish/quota.py`), 화면 표시는 KST

**만들 것 (제안)**
- 8.1 폴링: 작업별 다음 확인 시각(예: `next_check_at`, 마이그레이션 0012)을 두고 1 → 2 → 4 → 8 → 10분으로 늘린다. `PUBLISHED` 뒤에도 하루 정도 몇 번 확인해 거부·삭제(`rejected`, 영상 없음)를 잡는다. 상태 확인은 1 unit씩 할당량에 센다(지금도 `quota.add(…, 1)`)
- 8.2 재시도: 썸네일 실패 자동 재시도(2~3회), `uploadLimitExceeded`의 실제 풀리는 시점 확인(태평양 자정인지, 24시간 이동 창인지 — 지금은 자정으로 가정), 오류 종류·다음 시도 시각을 화면에 분명히
- 8.3 원격 수정 (**자동 반영으로 결정**, 2026-11-21): 업로드된 클립에서 제목·설명·태그·해시태그·공개 범위·예약을 고칠 수 있게 하고(구간·썸네일 프레임은 영상이 바뀌므로 지금처럼 취소 후 다시), 저장하면 publish 큐에서 `videos.update`로 반영한다. 썸네일 문구를 바꾸면 `thumbnails.set`도 다시. 업로드 중이면 끝난 뒤 반영, 반영 실패는 작업에 경고·다시 시도. 게시된 영상의 예약 변경은 막는다. update도 할당량(50 units)에 센다. 감사 로그에 남긴다
- 8.4 알림: 외부 알림은 만들지 않는다(2026-11-21 결정). 게시 현황에서 실패·재연결 필요·미뤄짐이 눈에 띄게(필터, 건수) 정도만
- 8.5 장애 주입 테스트: 시나리오 표(네트워크 끊김 → 이어 올리기, 세션 만료 → 처음부터, 401 → 갱신 → 성공/재연결 필요, quotaExceeded → 다음 날, uploadLimitExceeded → 그 채널만, 5xx 5회 → 실패, 썸네일 실패 → 영상 유지, 처리 거부 → 실패·새 영상으로 재시도, 워커 중단 → 멈춘 업로드 재시작)마다 상태 전이를 확인

**사용자에게 받을 것 / 정할 것**
- ~~알림 경로~~ → 외부 알림 없음, ~~메타 수정 반영 방식~~ → 자동 반영 (2026-11-21 사용자 결정)
- YouTube 운영 한도(spec 16-7): 사용자가 나중에 확인한다(Cloud 콘솔의 실제 units 사용량 → `YOUTUBE_QUOTA_INSERT` 조정, 운영 채널의 하루 업로드 개수 한도, 감사·할당량 증설 신청). M8은 지금 설정값으로 진행
- 테스트 채널에 남은 영상 정리(2026-11-21 테스트: 합성 클립 1건 공개, 11-16 기사 9건 비공개)

**클라우드 세션에서 실제 YouTube로 확인할 때**
- 환경 news-clipper에 `oauth2.googleapis.com`(OAuth 클라이언트 시크릿을 Basic으로 주입하는 네트워크 비밀 포함), `www.googleapis.com`이 허용돼 있다(2026-11-21). OAuth 클라이언트 ID는 `95445769906-ksq2gtof18b82eau3iqk2sftmhbjche2.apps.googleusercontent.com`("TV 및 입력 제한 기기")
- 채널 토큰은 세션 안 DB에만 있었으므로 새 세션에서는 채널을 다시 연결한다(기기 코드 방식, 사용자가 google.com/device에 코드 입력). `TOKEN_ENCRYPTION_KEY`는 새로 만들면 된다
- 실행 예: `PUBLISHER=youtube GOOGLE_CLIENT_ID=… GOOGLE_CLIENT_SECRET=proxy-injected TOKEN_ENCRYPTION_KEY=… NEWSROOM_PROVIDER=marsl MARSL_API_KEY=proxy-injected` + SQLite·`CELERY_ALWAYS_EAGER=true`면 워커 없이 확인할 수 있다(이때 beat가 없으므로 미뤄진 업로드·상태 확인은 `service.sweep()`이나 `/check`를 직접 부른다)

**기존 남은 일 (M8과 별개)**
- 로그인(M0.7 보류): 채널 연결·해제와 영상 삭제가 누구에게나 열려 있다. 관리자 권한과 함께 정할 것(M9.1과 묶어도 된다)
- M3.8 원래 골든셋 4편 재현, M4 실제 Claude 호출(키 필요), HD 원본으로 썸네일 판정·렌더 시간 확인, 리포트 썸네일 템플릿 PNG 교체(사용자 제공 예정)
- 이 브랜치를 `main`에 합칠 PR

---

## M9. 운영 준비
| # | Task | 지금 있는 것 |
|---|---|---|
| 9.1 | 관리자 화면: 사용자, 채널, 뉴스룸 연동 설정, 프로그램 프로필(사인오프 패턴, 제목 정리 규칙, 템플릿) | 채널 화면(`/channels`: 연결·해제·프로그램 기본 채널)만 있음. 프로그램 프로필은 API(`/api/programs`, 썸네일 오버레이 업로드 포함)와 업로드 폼의 "프로그램 추가"뿐. 사용자·로그인 없음(M0.7 보류) |
| 9.2 | 보존 기간에 따른 정리 작업 | 없음. 원본·프록시·렌더가 `storage/jobs/{id}/`에 계속 쌓인다 |
| 9.3 | 메트릭 대시보드: 단계별 처리 시간, 정렬 신뢰도, 검수 수정률, LLM 토큰 | 화면 없음. 데이터는 있음(아래 가이드) |
| 9.4 | 배포 문서, Postgres 백업, HTTPS 리버스 프록시 | Compose 한 대 구성, README 실행 절차, `.env.example`. HTTPS·백업·배포 문서 없음 |
| 9.5 | 실제 방송 5편 이상 파일럿 → 골든셋 확대 → 파라미터 튜닝 | 채점 도구(`eval/tools/score_alignment.py`), 골든셋 4편(녹화본은 세션마다 다시 받아야 함). 11-16 실제 방송으로 업로드까지 한 번 돌려 봄(M7) |

**DoD (제안)**: 운영 서버에 HTTPS로 배포되고, 백업·정리 작업이 돌고, 실제 방송 5편 이상을 파일럿으로 처리해 정렬·검수 지표를 기록한다.

### M9 시작 가이드 (새 세션용, 2026-11-21 작성)

**먼저 읽을 것**: spec 2(역할: 편집자·승인자·관리자, 사내 계정 인증), 6.2(저장 구조·보존 기간: 원본 30일, 프록시·분석 90일, 최종 클립은 업로드 성공 후 30일), 9(비기능: 성능, 보안 — 미디어 URL 인증, HTTPS), 11(품질 평가 지표), 16(미결 사항). 이 파일의 M0(로그인 보류), M7·M8 "정한 것"

**지금 상태 (M0~M8 완료, `main`에 PR #20까지 병합)**
- 파이프라인 전체가 된다: 업로드 → 분석 → 큐시트 → 정렬 → 메타·썸네일 → 검수 → 렌더 → YouTube 업로드·예약 → 상태 확인·재시도·수정 반영. 화면: 작업 목록·상세, 녹화본 올리기, 클립 편집기, 게시 현황, 채널
- **사용자·권한 (9.1)**: `User` 모델과 `Role`(editor/publisher/admin)만 있다. 지금은 화면 상단에 이름을 넣으면 `X-Reviewer` 헤더로 보내고, 서버가 이름마다 User를 만든다(`api/deps.current_reviewer`, `review/service.reviewer`). **누구나** 승인·채널 연결·영상 삭제를 할 수 있다. 로그인을 만들면 이 의존성만 바꾸면 되도록 해 두었다(`Reviewer` 타입). 권한이 필요한 API: 승인·반려(publisher), 업로드 취소(영상 삭제)·재시도(publisher), 채널 연결·해제·프로그램 프로필(admin)
- **프로그램 프로필 (9.1)**: `models/program.py`. 항목: 이름, 방송 시각, 오디오 트랙, 발음 사전, 전날 방송분 제외 프로그램, 제외 방송사, 제목 템플릿, 설명 꼬리말, 기본 태그, 썸네일 템플릿(JSON + 오버레이 PNG), 기본 채널. `PUT /api/programs/{id}`는 **전체를 바꾸는** 방식이라(빠진 칸은 비워짐) 화면에서 쓸 때 전체를 보내야 한다(채널 화면의 `useSetProgramChannels` 참고). `signoff_pattern`, `title_cleanup_rules`, `newsroom_program_id` 칼럼은 있지만 쓰는 코드가 없다
- **저장소 (9.2)**: `storage.py`의 `JobFiles` — `source/`(원본), `proxy/`, `audio/`, `analysis/`, `newsroom/`, `clips/{id}/`(렌더·썸네일). 업로드 중 파일은 `storage/uploads/`(tus, 끝나지 않은 업로드도 남는다). 정리 기준이 될 시각: `Job.created_at`, `Clip.rendered_at`, `PublishTask.published_at`/`status`. 렌더를 지운 클립은 다시 렌더할 수 있어야 하는지(원본이 남아 있을 때만 가능) 정할 것. 주기 작업은 Celery beat(`worker.py`의 `beat_schedule`)에 붙이면 된다
- **메트릭 데이터 (9.3)**: 단계 처리 시간 `JobStep.started_at/finished_at/attempts`, 정렬 신뢰도 `Clip.confidence`·`flags`·`AlignedSentence.score`·`RundownItem.align_status`, 검수 수정률 `Clip.start_snap/end_snap == "manual"`·`AuditLog`(action: range, metadata, thumbnail …), LLM 토큰 `ClipMetadata.llm_input_tokens/llm_output_tokens`, 업로드 `PublishTask`(시도 횟수, 오류 종류), 할당량 `QuotaUsage`. 로그는 JSON(`core/logging.py`)
- **배포 (9.4)**: `docker-compose.yml`(postgres, redis, migrate, api, 워커 4개, beat, web — web만 포트 8080 노출), `frontend/nginx.conf`(SPA + `/api`·`/media` 프록시, SSE, 100MB 청크). 볼륨: `pgdata`, `redisdata`, `storage`, `models`(정렬 모델 1.2GB). 이미지 빌드는 CI(`docker` 작업)에서 확인된다. `/media` URL은 인증 없이 열린다(spec 9의 "미디어 URL 인증"은 로그인과 함께)
- **파일럿 (9.5)**: 녹화본·큐시트·정답은 `eval/data/`(커밋 안 함). `eval/tools/score_alignment.py`가 서비스 정렬을 `ground_truth.json`과 비교한다. 원래 골든셋 4편(11-02, 11-04, 11-05, 11-11)은 다시 받아야 하고, 11-16·11-19도 정렬을 돌려 본 적이 있다. **블라인드 원칙**: 정답을 먼저 정하고 결과를 본다(CLAUDE.md "작업 방식")

**만들 것 (제안, 범위는 사용자와 정할 것)**
- 9.1: 로그인(ID/PW, 세션 쿠키 또는 토큰) + 역할 검사 + 사용자 관리 화면, 프로그램 프로필 편집 화면(제목 템플릿·꼬리말·태그·썸네일 템플릿 미리보기·발음 사전·제외 방송사·기본 채널). 로그인을 하지 않기로 하면 최소한 관리자 기능(채널·프로필)만 비밀번호로 막는 안도 있다
- 9.2: 정리 작업(beat, 하루 한 번): 보존 기간이 지난 원본·프록시·분석·렌더 파일 삭제, 끝나지 않은 tus 업로드 삭제, 지운 것 기록(작업 화면에 "원본 삭제됨" 표시). 설정값으로 기간 조정, 먼저 "지울 목록 보기"(dry run)
- 9.3: 운영 지표 화면 또는 주간 요약: 방송별 처리 시간, 검출률(구간 자동/수동), 검수 수정률, 업로드 성공률·재시도, 할당량 사용
- 9.4: 배포 문서(서버 준비, `.env`, 첫 실행, 업데이트·롤백, 로그 보기), PostgreSQL 백업(`pg_dump` 정기 실행 + 보관 기간, 복원 확인), HTTPS(사내 리버스 프록시나 인증서 방식에 맞춰), 운영 설정 점검표(`ENABLE_DEV_ENDPOINTS=false`, 키·비밀, `PUBLISHER=youtube`, `TOKEN_ENCRYPTION_KEY` 백업)
- 9.5: 실제 방송 5편 이상(뉴스데스크·뉴스투데이 섞어서)을 운영과 같은 설정으로 처리 → 정답 표시 → 채점 → 필요하면 파라미터 조정(바꾸면 골든셋 전체 재확인) → 결과를 `eval/poc/RESULTS.md`나 새 문서에 기록

**사용자에게 받을 것 / 정할 것**
- 로그인·권한이 필요한지(사용자 수, 사내 계정 연동 여부), 아니면 지금처럼 이름만으로 쓸지
- 운영 서버: 사양(CPU 8코어·RAM 16GB 이상 권장, 디스크 — 원본 보관 기간에 따라), OS, Docker 사용 가능 여부, 사내망 주소·HTTPS 방식(도메인·인증서), `api.marsl.co.kr`·Google API 접근(방화벽)
- 보존 기간 확정(spec 6.2 제안: 원본 30일, 프록시·분석 90일, 렌더는 업로드 성공 후 30일)과 백업 위치
- 파일럿 방송 녹화본(HD 원본 포함 — M6 렌더 시간, M4 썸네일 확인도 함께)과 정답 표시를 누가 할지
- 남아 있는 준비물: Anthropic API 키(M4), 리포트 썸네일 템플릿 PNG(M4), YouTube 운영 한도 확인(spec 16-7), 운영 채널 연결

**기존 남은 일 (M9와 함께 처리하면 좋은 것)**
- M3.8 원래 골든셋 4편 재현, M4 실제 Claude 호출·HD 녹화본으로 썸네일 판정, M6 HD 원본 렌더 시간(spec 9: 클립 1건 ≤ 1분)
- 테스트 채널에 남은 테스트 영상 10건 정리(사용자 결정 대기)

---

## M10+ (P2 확장)
- 부분 STT 보조 (`LOW_CONFIDENCE`/`NOT_FOUND` 구간)
- 정렬된 원고로 YouTube 자막(SRT) 업로드
- 기타 SNS 어댑터, 세로형 9:16 리프레이밍
- 신뢰도 기반 자동 승인, SSO

---

## 일정 (1인 기준 추정)
| 마일스톤 | 기간 | 비고 |
|---|---|---|
| M-PoC | 완료 | 방송 4편 검증 |
| M0 | 완료 | 로그인(0.7) 보류 |
| M1 | 완료 | |
| M2 | 완료 | |
| M3 | 완료 | 원래 골든셋 4편 재현 남음 |
| M4 | 완료 | 실제 Claude 호출·HD 녹화본 확인 남음 |
| M5 | 완료 | |
| M6 | 완료 | HD 원본 렌더 시간 확인 남음 |
| M7 | 완료 | 운영 채널 한도·할당량 확인 남음 |
| M8 | 완료 | 외부 알림은 하지 않음 |
| M9 | 4일 + 파일럿 | 다음 작업. 범위(로그인 여부, 배포 환경)에 따라 달라짐 |
| **남은 일정** | **약 4일** | M9 (실제 운영 확인 제외) |

## 선행 준비물
- [x] 골든셋 방송 4편(11-02, 11-04, 11-05, 11-11)의 큐시트·원고·녹화본 — 단, `eval/data/`는 git에 없으므로 M3 회귀 확인 때 다시 올려야 함
- [x] MARS-L API 명세 (지역MBC-NRCS-B106 V0.2)
- [x] MARS-L 액세스 키, 실제 응답 샘플 (M2.10, 2026-11-20)
- [ ] 사내 앱 서버에서 `api.marsl.co.kr` 접근 확인 (클라우드 세션에서는 됨)
- [ ] Anthropic API 키 (M4 실제 호출 확인용, 없으면 원고 기반 기본값으로 진행). 받을 때는 `request_credential`(호스트 `api.anthropic.com`, 헤더 `x-api-key`)
- [x] YouTube 테스트 채널 + Google Cloud OAuth 클라이언트 (M7, 2026-11-21. "TV 및 입력 제한 기기" 유형)
- [ ] YouTube API 할당량 증설·감사 신청 — 승인에 수 주가 걸릴 수 있어 **조기 신청**. 신청 전에 Cloud 콘솔의 실제 units 사용량과 운영 채널의 하루 업로드 개수 한도를 확인(spec 16-7)
- [ ] 정렬 워커용 서버 사양 확인 (CPU 8코어, RAM 16GB 이상 권장)
