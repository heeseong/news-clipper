# News Clipper — 구현 계획 (plan.md)

> spec.md v1.0 기준 (큐시트·원고 + 강제 정렬, 자사 리포트만 업로드). 각 마일스톤은 독립적으로 실행·검증할 수 있는 단위이며, 완료 조건(DoD)을 만족하면 다음으로 넘어간다.

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
      providers/      # llm/, publishers/, stt/(P2)
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

## M2. 큐시트·원고 연동 및 파서 (파이프라인 4) — 다음 작업
> **상태**: 파서·판별 핵심 로직(2.4~2.8, 2.11)은 `backend/app/newsroom/`에 구현돼 있고 단위 테스트가 있다. Provider, DB 모델, 파이프라인 단계, API, 화면 연결이 남았다.

| # | Task | 상태 |
|---|---|---|
| 2.1 | `NewsroomProvider` 인터페이스 + `FakeNewsroomProvider` (MARS-L 실제 응답 형식 그대로인 **합성** JSON 픽스처) | 남음 |
| 2.2 | `MarsLProvider`: IF_MARSL_001~003 클라이언트(004는 필요 없음, 아래), 인증 헤더 `Mars-Secret`, 공통 오류 응답 처리, 재시도(429·5xx·타임아웃), 필드 별칭·공백·날짜 형식 정규화, `programType == "TV"` 필터 | 남음 |
| 2.3 | Rundown / RundownItem DB 모델, 큐시트 매칭(방송일 + 프로그램명 trim + 시작 시각), 원본 응답 저장, `cueSheetVersion` 기반 최종판 확인·판 변경 차이 기록 | 남음 |
| 2.4 | **원고 파서**: 섹션, 인터뷰·스탠드업·사인오프 구분, 발화 아닌 요소 제거, 끊긴 줄 복원, 문장 분할 | 구현됨 (`script_parser.py`) |
| 2.5 | 메모 파싱(썸네일·상단·수퍼), 제목 정리(`R]`, `(R)` 등) | 구현됨. 단 실제 API의 `memo`는 비어 있음(spec 3.1) |
| 2.6 | **형식(R/S) 판별**: `remark` → `articleTypeCode`(003의 `article`에 들어 있음) → 원고 구조. 판별 근거 저장 | 구현됨 (`rundown.classify`). 004 매칭은 필요 없음 |
| 2.7 | **예정 길이**: `ancMentCttTime + artclCttTime + artclExtTime`, 없으면 원고 글자 수로 추정 | 구현됨 (`planned_from_article`, `estimate_seconds`) |
| 2.8 | 파서·판별 단위 테스트 | 구현됨 (`tests/test_script_parser.py`) |
| 2.9 | 업로드 폼의 큐시트 미리보기 API + UI (판 수, 리포트 수, 예정 총 길이, 큐시트 없으면 경고), `WAITING_RUNDOWN` 상태와 수동 재조회 | 남음 |
| 2.10 | 실제 API로 큐시트 조회해 파서 검증 (액세스 키 필요) | 응답 형식은 확인됨(11-01, 11-04, spec 3.1). 서비스 코드로 실제 호출은 남음 |
| 2.11 | 다른 방송사 리포트 제외(`isSameChannel`/`originChannelName` 우선, 제목 `광주)`, 기자 `이름(광주)`) | 로직 구현됨. 제외 목록을 `ProgramProfile.excluded_sources`와 연결하는 일이 남음 (지금은 `rundown.EXCLUDED_SOURCES` 상수) |
| 2.12 | 제목 정리 보완: `(R-1)`, `/기자명` 접미어 제거, 큐시트 기자(데스크)와 사인오프 기자가 다를 때 사인오프 기자 사용 | 남음 (`(R-1)`은 이미 처리됨) |

**DoD**: Fake 데이터로 업로드 폼에서 큐시트가 미리 보이고, 리포트 아이템의 원고가 리드·본문·사인오프·메모로 파싱돼 저장된다. 작업의 `newsroom` 단계가 SKIPPED가 아니라 실제로 실행된다.

### M2 시작 가이드 (새 세션용)

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
- MARS-L 액세스 키: 실제 API 호출(2.10)에만 필요. Fake로 DoD까지는 키 없이 진행할 수 있다
  - 이 클라우드 환경(news-clipper)은 `api.marsl.co.kr`이 허용 호스트에 없고 저장된 키도 없다. 실제 호출이 필요해지면 환경에 호스트 추가를 요청하고, 키는 채팅이 아니라 자격 증명 입력 양식으로 받는다(헤더 `Mars-Secret`)
  - 앱 서버에서 `api.marsl.co.kr`로 나갈 수 있는지(IP 허용 목록 여부)는 미결 (spec 16)
- 실제 큐시트·원고(PDF 출력물 또는 API 응답 JSON): 파서 검증용. `eval/data/`에 두고 커밋하지 않는다. 지난 세션의 `eval/data/`는 남아 있지 않다

---

## M3. 정렬 (파이프라인 5)
> PoC 스크립트(`eval/poc/align_poc.py`)를 `backend/app/align/`의 서비스 코드로 옮기는 단계다. 규칙은 spec 5.5와 `eval/poc/RESULTS.md`를 따른다.

| # | Task |
|---|---|
| 3.1 | 로그 확률 계산 모듈: 모델 로딩(align 워커 상주, `HF_HUB_DISABLE_XET=1`로 다운로드), 20초 청크 + 1초 문맥, 결과(lpz) 캐시 저장 |
| 3.2 | 아이템 순차 정렬: 앞 아이템 마지막 문장을 기준점으로 함께 정렬, 첫·마지막 글자 시각 사용, 원고 없는 아이템 길이만큼 창 확장, 첫 아이템은 앞부분 여유 300초 |
| 3.3 | 판정: `LOW_CONFIDENCE`(문장 점수 중앙값 < -5 또는 -5 미만 30% 초과), `DURATION_MISMATCH`(예정 길이 ±20%), 판정 실패 시 뒤의 장면 컷부터 재정렬 |
| 3.4 | 경계 결정: 리드 시작 무음 보정(`LEAD_ONSET_IN_SILENCE`), 앵커 컷 스냅(+1프레임), 인사말 감지(컷~리드 사이 음절 4개 이상 → 무음에서 시작), 끝 = 다음 아이템 앵커 컷 - ½프레임 |
| 3.5 | 업로드 대상 리포트별 Clip(DRAFT) 생성, `NOT_FOUND`·판정 실패 리포트는 수동 지정 대상으로 생성, 방송 단위 불일치 경고(아이템 절반 이상 실패) |
| 3.6 | 평가 도구: 골든셋 정답 형식(`ground_truth.json`), 경계 오차·판정 결과 리포트 (`eval/poc/align_poc.py`의 채점 부분 이전) |
| 3.7 | 단위 테스트: 경계 결정·판정 로직을 합성 데이터(가짜 lpz, 컷·무음 목록)로 테스트. 실제 모델은 테스트에서 쓰지 않음 |
| 3.8 | 회귀 확인: 골든셋 4편을 다시 올려(`eval/data/`는 git에 없음) 34/34 유지 확인 |

**DoD**: 골든셋 4편에서 PoC와 같은 결과(34/34, 불일치 조합 전부 거부)가 서비스 코드로 재현된다.

---

## M4. 메타데이터·썸네일 (파이프라인 7)
| # | Task |
|---|---|
| 4.1 | 원고 기반 기본값: 제목(정리된 원고 제목), 설명(앵커 리드 + 템플릿 꼬리말), 태그(기본 태그 + 기자명), 썸네일 문구(원고 메모) |
| 4.2 | `LLMProvider` 인터페이스 + Claude 구현 + Fake: 대안 제목 2개, 본문 요약, 키워드·해시태그 (structured output) |
| 4.3 | 사실성 검사: LLM 결과의 고유명사·숫자가 원고에 있는지 확인 |
| 4.4 | 썸네일 후보 추출(원본에서, 프록시 아님) + 점수(선명도/노출/얼굴, 앵커 샷 감점) → 상위 6개 |
| 4.5 | 썸네일 오버레이 렌더러(Pillow, 템플릿 JSON) → 1280×720 JPG ≤ 2MB |
| 4.6 | 메타 재생성 API |

**DoD**: 각 리포트 클립에 원고 기반 메타데이터, LLM 대안, 썸네일 후보 6개가 생기고 Job이 REVIEW_READY가 된다. Claude API 키가 없어도 원고 기반 기본값만으로 진행된다.

---

## M5. 검수 화면 (파이프라인 8)
| # | Task |
|---|---|
| 5.1 | 작업 상세: 전체 타임라인(스프라이트 + 파형 + 아이템 블록 + 경고) + 큐시트 패널(검출 상태) |
| 5.2 | 클립 편집기: 플레이어(프레임 이동, 단축키), IN/OUT 편집, 경계 반복 재생, 스냅 마커 |
| 5.3 | 원고 패널: 정렬 문장 하이라이트, 클릭 이동, 신뢰도 표시 |
| 5.4 | 메타데이터 폼(제목 후보, 글자 수), 썸네일 선택/현재 프레임/업로드, 오버레이 미리보기 |
| 5.5 | 미검출 리포트 수동 구간 지정 → 클립 생성 |
| 5.6 | 발행 설정(채널, 공개 범위, 예약 KST), 일괄 승인·순차 예약 |
| 5.7 | 승인 검증, 편집 잠금, 감사 로그 |
| 5.8 | Playwright E2E: 업로드 → 검수 → 승인 (Fake provider) |

**DoD**: 키보드 중심으로 리포트 1건을 1분 안에 검수·승인할 수 있고, E2E 테스트가 통과한다.

---

## M6. 최종 렌더 (파이프라인 6-b)
| # | Task |
|---|---|
| 6.1 | 원본에서 정확한 컷 + 재인코딩 + loudnorm 2-pass + 페이드 옵션 |
| 6.2 | 렌더 검증(길이 ±0.1초, 오디오 존재), 재시도 |
| 6.3 | 승인 시 렌더 자동 시작, 승인 후 경계 변경 시 렌더 무효화·재렌더 |
| 6.4 | 렌더 결과 미리보기·다운로드 (수동 게시용 대체 경로) |

**DoD**: 승인된 클립이 원본 화질로 렌더되고, 시작 프레임이 검수 화면에서 지정한 프레임과 같다는 것이 테스트로 검증된다.

---

## M7. YouTube 업로드·예약 발행 (파이프라인 9)
| # | Task |
|---|---|
| 7.1 | `Publisher` 인터페이스 + `FakePublisher` |
| 7.2 | Google OAuth 2.0 채널 연동, 토큰 암호화 저장, 갱신 |
| 7.3 | YouTube 어댑터: resumable upload(재개 URI 저장), publishAt 예약, 썸네일, 카테고리·언어 |
| 7.4 | 할당량 추적, 한도 도달 시 다음 날로 대기 |
| 7.5 | PublishTask 생성(승인 + 렌더 완료 → 업로드 큐), 업로드 대시보드 |

**DoD**: 테스트 채널에 비공개 예약 업로드가 성공하고, 예약 시각에 공개되며 썸네일과 메타데이터가 반영된다.

---

## M8. 상태 확인·재시도·알림 (파이프라인 10)
| # | Task |
|---|---|
| 8.1 | 상태 폴링(점진적 간격), processing/rejection 반영 |
| 8.2 | 오류 유형별 재시도 정책 + 지수 백오프 |
| 8.3 | 수동 재시도/취소/원격 메타 수정 |
| 8.4 | 웹 알림 + Slack/메일 Webhook(선택) |
| 8.5 | 장애 주입 테스트(네트워크 끊김, 401, quotaExceeded, 5xx) |

**DoD**: 장애 시나리오별 상태 전이와 재시도가 테스트로 검증된다.

---

## M9. 운영 준비
| # | Task |
|---|---|
| 9.1 | 관리자 화면: 사용자, 채널, 뉴스룸 연동 설정, 프로그램 프로필(사인오프 패턴, 제목 정리 규칙, 템플릿) |
| 9.2 | 보존 기간에 따른 정리 작업 |
| 9.3 | 메트릭 대시보드: 단계별 처리 시간, 정렬 신뢰도, 검수 수정률, LLM 토큰 |
| 9.4 | 배포 문서, Postgres 백업, HTTPS 리버스 프록시 |
| 9.5 | 실제 방송 5편 이상 파일럿 → 골든셋 확대 → 파라미터 튜닝 |

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
| M2 | 3일 | 다음 작업. 파서 핵심 로직은 구현됨. 실제 API 검증(2.10)은 액세스 키 확보 후 |
| M3 | 5일 | PoC 코드 이전 + 회귀 확인 |
| M4 | 3일 | |
| M5 | 7일 | |
| M6 | 2일 | |
| M7 | 4일 | |
| M8 | 3일 | |
| M9 | 4일 | |
| **합계** | **약 38일 (약 8주)** | |

## 선행 준비물
- [x] 골든셋 방송 4편(11-02, 11-04, 11-05, 11-11)의 큐시트·원고·녹화본 — 단, `eval/data/`는 git에 없으므로 M3 회귀 확인 때 다시 올려야 함
- [x] MARS-L API 명세 (지역MBC-NRCS-B106 V0.2)
- [ ] MARS-L 액세스 키, 앱 서버에서의 네트워크 접근, 실제 응답 샘플 1건 (M2.10)
- [ ] Anthropic API 키 (M4, 없으면 원고 기반 기본값으로 진행)
- [ ] YouTube 테스트 채널 + Google Cloud OAuth 클라이언트 (M7)
- [ ] YouTube API 할당량 증설 신청 — 승인에 수 주가 걸릴 수 있어 **조기 신청**
- [ ] 정렬 워커용 서버 사양 확인 (CPU 8코어, RAM 16GB 이상 권장)
