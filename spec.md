# News Clipper — 기능 명세 (spec.md)

> 종합뉴스 방송 원본을 리포트 단위로 자동 분할하고, 제목·설명·썸네일을 생성한 뒤 검수를 거쳐 YouTube 등 채널에 예약 업로드하는 사내 웹 애플리케이션.

- 문서 버전: v0.1 (2026-11-18)
- 상태: 초안 (섹션 14의 미결 사항 확인 필요)

---

## 1. 배경 및 목표

### 1.1 문제
- 종합뉴스(30~90분) 방송이 끝나면 편집자가 리포트마다 수동으로 IN/OUT을 찾고, 자르고, 제목과 썸네일을 만들어 업로드한다. 방송 한 편에 리포트가 15~30개라 시간이 많이 든다.

### 1.2 목표
| 목표 | 지표 |
|---|---|
| 리포트 분할 자동화 | 자동 탐지 경계의 90% 이상이 실제 경계에서 ±1초 이내 |
| 처리 시간 단축 | 60분 원본 업로드 완료 후 15분 안에 검수 화면 진입 |
| 검수 부담 최소화 | 리포트 1건 검수(경계 확인, 메타 수정, 승인)를 평균 1분 이내에 |
| 안정적인 배포 | 업로드 실패 시 자동 재시도, 최종 성공률 99% 이상 |

### 1.3 범위 밖 (v1)
- 생방송 스트림 실시간 분할
- 자막(SRT) 번인 및 영상 편집 효과(자막 바, 로고 삽입 등). 단, STT 결과로 만든 SRT를 YouTube 자막 트랙으로 올리는 것은 선택 기능(P2)
- 검수 없는 완전 자동 업로드 (신뢰도 기반 자동 승인은 추후 확장)
- 대담·시사 프로그램 분할 (v1은 앵커 + 리포트 VCR 구조의 종합뉴스만 대상)

---

## 2. 사용자 및 역할

| 역할 | 권한 |
|---|---|
| 편집자 (Editor) | 원본 업로드, 분할 결과 검수·수정, 메타데이터 편집, 승인 요청 |
| 승인자 (Publisher) | 편집자 권한 + 최종 승인, 예약 발행, 업로드 재시도·취소 |
| 관리자 (Admin) | 사용자, 채널 연동(OAuth), 프롬프트·템플릿, 시스템 설정 관리 |

- 사내 소규모 운영: 동시 사용자 10명 이하, 하루 원본 수~수십 편
- 인증: 사내 계정(ID/PW) 로그인. SSO는 추후 확장

---

## 3. 전체 파이프라인

```
[1 수집] → [2 프록시 변환] → [3 A/V 분리] → [4 STT] → [5 경계 탐지]
   → [6 클립 후보 생성] → [7 메타·썸네일 생성] → [8 검수/승인]
   → [6' 최종 렌더(원본 화질)] → [9 업로드·예약 발행] → [10 상태 확인·재시도]
```

- 2~7단계는 업로드가 끝나면 자동으로 순서대로 실행된다.
- 6단계는 "클립 후보(구간 정보 + 프록시 미리보기)"까지만 만든다. **원본 화질의 최종 렌더는 검수 승인 뒤(6')** 수행한다. 검수 중 경계가 바뀌어도 다시 렌더할 필요가 없어 낭비가 없다.
- 각 단계는 독립된 비동기 작업(Task)이다. 실패하면 해당 단계부터 다시 실행할 수 있다(멱등성 보장).

### 3.1 작업(Job) 상태
```
UPLOADING → UPLOADED → TRANSCODING → EXTRACTING → TRANSCRIBING
→ SEGMENTING → GENERATING → REVIEW_READY → (클립별 진행) → DONE
                           ↘ FAILED (단계 정보 + 오류 메시지, 재시도 가능)
```

### 3.2 클립(Clip) 상태
```
DRAFT → (편집) → APPROVED → RENDERING → RENDERED → SCHEDULED/UPLOADING
→ UPLOADED → PROCESSING(플랫폼 처리 중) → PUBLISHED
            ↘ REJECTED (업로드 제외)
            ↘ UPLOAD_FAILED (자동/수동 재시도)
```

---

## 4. 단계별 기능 명세

### 4.1 [1] 원본 영상 수집
- **웹 드래그 앤 드롭**으로 업로드한다. 파일 선택 버튼도 함께 제공한다.
- 지원 형식: MP4, MOV, MXF, MKV, TS (컨테이너와 코덱은 FFmpeg로 디코딩 가능한 것)
- 최대 크기: 50GB (설정값)
- **분할·재개 가능한 업로드(tus 프로토콜)**: 네트워크가 끊기면 이어서 올리고, 진행률과 남은 시간을 표시한다.
- 업로드할 때 입력하는 메타데이터:
  - 프로그램명(예: "저녁 종합뉴스") — 미리 등록한 프로그램 프로필에서 선택
  - 방송일시 (기본값: 파일 생성일)
  - 대상 채널(들)
- 업로드가 끝나면 `ffprobe`로 검증한다(길이, 해상도, 프레임레이트, 오디오 트랙 수). 손상된 파일이면 거부한다.
- 같은 파일을 중복 업로드하면 경고한다(파일 해시 비교).

**프로그램 프로필** (관리자가 설정)
- 오프닝/클로징 처리 방식, 분할 제외 코너(날씨, 광고 등)
- 제목·설명 템플릿, 기본 태그, 썸네일 템플릿
- 기본 업로드 채널과 공개 설정

### 4.2 [2] 프록시 영상 변환
- 검수 화면 재생과 분석용 경량 영상을 만든다.
  - 720p, H.264, CRF 28, **GOP 1초(키프레임 간격 = fps)** — 웹 플레이어에서 정확하고 빠르게 탐색하기 위함
  - AAC 128kbps, `+faststart`
- 비월주사(interlaced) 원본이면 디인터레이스(`yadif`)를 적용한다.
- 원본의 타임코드와 프록시 시간축이 **1:1로 일치**해야 한다(fps 유지, 시작 PTS 0으로 정규화).
- 함께 생성하는 것: 타임라인 스크럽용 썸네일 스프라이트(1초 간격), 오디오 파형 데이터(JSON)

### 4.3 [3] 오디오 및 영상 분리
- STT 입력용 오디오: 16kHz mono FLAC
- 오디오 트랙이 여러 개인 방송 원본(MXF 등)은 프로그램 프로필에 지정된 트랙(기본: 1번 트랙, 또는 1+2 믹스다운)을 사용한다.
- 분석용 신호를 추출한다:
  - **무음 구간**: `silencedetect` (기본 -35dB, 0.3초 이상)
  - **장면 전환**: PySceneDetect(ContentDetector)로 장면 전환 시점 목록 생성
  - (선택) 음량 정규화 측정값 — 최종 렌더의 loudnorm 2-pass에 재사용

### 4.4 [4] 오디오 분석 (STT)
- 클라우드 STT API를 사용한다. **Provider 인터페이스로 추상화**해 교체할 수 있게 한다.
  - 1차 구현: 단어 단위 타임스탬프를 지원하는 한국어 STT (후보: OpenAI Whisper API / gpt-4o-transcribe 계열, 네이버 Clova Speech)
- 긴 오디오는 **10분 단위 청크 + 5초 오버랩**으로 나눠 병렬 요청하고, 오버랩 구간은 타임스탬프 기준으로 중복 제거해 병합한다.
- 출력(Transcript):
  - 문장/발화 세그먼트 목록: `{id, start, end, text, confidence}`
  - 단어 목록: `{start, end, word}` (경계 미세 조정에 사용)
  - (지원 시) 화자 분리 라벨 — 앵커와 기자를 구분하는 데 활용
- 도메인 용어 사전(인명, 지명, 기자명)을 프로그램 프로필에 등록하고, 지원하는 STT라면 힌트로 전달한다.
- 실패 시 청크 단위로 재시도한다(최대 3회, 지수 백오프).

### 4.5 [5] 리포트별 시작·종료 시점 탐지 (STT + LLM 하이브리드)

**5-1. LLM 의미 분할 (Claude API)**
- 입력: 세그먼트 ID와 타임스탬프가 포함된 전사문, 프로그램 프로필 정보
- LLM이 반환하는 것(JSON Schema 강제, tool use / structured output):
  ```json
  {
    "items": [{
      "type": "report | brief | anchor_only | weather | sports | opening | closing | ad | other",
      "start_segment_id": 12,
      "end_segment_id": 47,
      "anchor_lead_start_segment_id": 12,
      "reporter_name": "홍길동",
      "headline": "…",
      "summary": "…",
      "confidence": 0.0
    }]
  }
  ```
- 판단 근거로 쓰는 뉴스 문법: 앵커 리드 → 기자 리포트 → 클로징 멘트 "○○○ 뉴스 ○○○입니다" / "○○○ 기자가 보도합니다"
- 긴 전사문은 **겹치는 윈도우(약 20분, 2분 오버랩)**로 나눠 처리하고, 경계가 겹치는 항목은 병합한다.

**5-2. 신호 기반 경계 보정**
- LLM이 정한 세그먼트 경계 시각에서 **±2초 창** 안의 후보 지점을 찾는다:
  1. 장면 전환 지점 (영상 컷)
  2. 무음 구간의 중앙
  3. 단어 타임스탬프의 간격(단어 사이 빈틈)
- 우선순위: `장면 전환 ∩ 무음` > `무음` > `장면 전환` > `단어 간격` > LLM 원값
- 시작점은 앞쪽, 종료점은 뒤쪽으로 **패딩(기본 0.3초)**을 둬 말이 잘리지 않게 한다.
- 결과로 각 경계에 `boundary_confidence`와 근거(`snapped_to: scene|silence|word|raw`)를 저장한다.

**5-3. 규칙**
- 기본으로 `report` 타입만 클립 후보로 만든다. `brief`(단신)·`weather`·`sports`의 포함 여부는 프로그램 프로필에서 설정한다.
- **앵커 리드를 포함한다**: 클립 시작 = 앵커 리드 시작, 클립 종료 = 기자 클로징 멘트 종료
- 최소 길이 30초, 최대 길이 10분. 범위를 벗어나면 경고 플래그를 붙인다.
- 인접 클립과 구간이 겹치거나 사이에 빈틈이 있으면 경고 플래그를 붙인다.

### 4.6 [6] 영상 분할
- **6-a. 클립 후보 (자동, 검수 전)**: DB에 구간 정보(`start`, `end`)만 저장한다. 미리보기는 프록시 영상의 해당 구간을 플레이어에서 재생하는 방식이라 파일을 따로 만들지 않는다.
- **6-b. 최종 렌더 (승인 후)**: 원본에서 프레임 단위로 정확하게 자른다.
  - 재인코딩(키프레임에 의존하지 않는 정확한 컷): H.264 High, 원본 해상도(최대 1080p, 설정값), CRF 18 / preset medium, AAC 192kbps 48kHz, `+faststart`
  - 오디오 음량 정규화: EBU R128 `loudnorm` (기본 -14 LUFS, 플랫폼 권장값)
  - 선택: 시작 0.2초 페이드인 / 끝 0.3초 페이드아웃
  - 렌더 결과는 `ffprobe`로 길이를 검증한다(허용 오차 ±0.1초).

### 4.7 [7] 제목·설명·썸네일 생성

**제목/설명/태그 (Claude API)**
- 입력: 클립 전사문, 리포트 요약, 기자명, 방송일, 프로그램 프로필의 템플릿과 스타일 가이드
- 출력:
  - 제목 후보 3개 (YouTube 제한 100자, 권장 40~60자, 클릭베이트·과장 표현 금지 규칙을 프롬프트에 명시)
  - 설명: 요약 2~3문장 + 템플릿 꼬리말(채널 정보, 기자명, 방송일, 저작권 문구)
  - 태그 10개 이내, 해시태그 3개 이내
- 사실성 가드레일: 전사문에 없는 인명·수치는 만들지 않도록 지시한다. 생성 결과 속 고유명사가 전사문에 있는지 검사해 없으면 경고를 표시한다.
- 출력 언어: 한국어

**썸네일**
- 후보 프레임 추출: 장면 전환 직후 프레임 + 균등 샘플링 → 품질 점수로 상위 6개 선정
  - 점수: 선명도(Laplacian variance), 밝기·노출, 얼굴 검출 여부/크기, 앵커 샷 감점(리포트 본문 화면 우대)
- 썸네일 템플릿(프로그램 프로필): 텍스트 오버레이(제목 키워드), 채널 로고, 폰트·색상
  - 오버레이 문구는 LLM이 15자 이내 핵심 문구로 생성하고, 검수에서 수정할 수 있다
- 출력: 1280×720 JPG, 2MB 이하 (YouTube 규격)
- 원본 화질 프레임을 사용한다(프록시 프레임 사용 금지).

### 4.8 [8] 검수 화면
**목록 화면 (작업 단위)**
- 작업 목록: 프로그램명, 방송일, 상태, 진행률, 클립 수(승인/전체)
- 작업 상세: 전체 타임라인 위에 클립 구간을 색상 블록으로 표시(타입별 색상), 경고 플래그 아이콘

**클립 편집 화면**
- 영상 플레이어(프록시): 재생/일시정지, 프레임 단위 이동(←/→), 1초 이동(Shift+←/→), 재생 속도 조절
- 경계 편집:
  - IN/OUT 지점 드래그 또는 단축키(`I`/`O`)로 지정
  - 경계 주변 ±3초 반복 재생(경계 확인용)
  - 후보 스냅 지점(장면 전환/무음) 마커 표시, 클릭하면 스냅
- 클립 조작: 분할(현재 위치에서 둘로), 병합(인접 클립), 삭제(REJECTED), 누락 구간을 새 클립으로 추가
- 전사문 패널: 재생 위치와 동기화해 하이라이트, 문장을 클릭하면 해당 위치로 이동
- 메타데이터 편집: 제목(후보 선택 또는 직접 입력, 글자 수 표시), 설명, 태그, 공개 설정
- 썸네일 선택: 후보 6개 중 선택 / 현재 프레임으로 지정 / 이미지 업로드, 오버레이 문구 편집 및 미리보기
- 발행 설정: 대상 채널, 즉시 공개 / 예약(날짜·시간, KST) / 비공개 / 일부 공개
- 일괄 처리: 여러 클립을 선택해 일괄 승인, 예약 시간 일괄 지정(예: 10분 간격으로 순차 예약)
- **변경 이력**: 누가 언제 무엇을 바꿨는지 감사 로그를 남긴다
- 동시 편집 방지: 클립 편집 잠금(다른 사용자가 편집 중이면 표시)

**승인**
- 승인하면 클립이 `APPROVED` 상태가 되고 최종 렌더(6-b)가 자동으로 시작된다
- 필수 검증: 제목 비어 있음 / 길이 초과 / 썸네일 미선택 / 경고 플래그 미확인 → 승인 불가

### 4.9 [9] 채널 업로드 및 예약 발행

**Publisher 어댑터 구조**
```python
class Publisher(Protocol):
    def upload(self, clip: RenderedClip, meta: PublishMeta) -> PublishResult: ...
    def set_thumbnail(self, remote_id: str, image: Path) -> None: ...
    def get_status(self, remote_id: str) -> RemoteStatus: ...
    def update(self, remote_id: str, meta: PublishMeta) -> None: ...
    def delete(self, remote_id: str) -> None: ...
```

**YouTube (v1 필수)**
- YouTube Data API v3
  - `videos.insert` **resumable upload** (청크 크기 8MB의 배수, 중단 지점에서 재개)
  - 예약 발행: `status.privacyStatus = "private"` + `status.publishAt`(ISO 8601, UTC로 변환)
  - `thumbnails.set` (채널 인증 필요)
  - `snippet.categoryId = 25` (News & Politics), `defaultLanguage = ko`
  - (P2) `captions.insert`로 STT 기반 SRT 자막 업로드
- 채널 연동: 관리자가 OAuth 2.0으로 채널을 연결한다. refresh token은 암호화해 저장한다.
- **할당량(Quota) 관리**: 기본 할당량은 하루 10,000 units이고 `videos.insert` 1회에 1,600 units가 들어 **하루 약 6건만 업로드할 수 있다**. 운영 전에 Google에 할당량 증설을 신청해야 한다. 시스템은 일일 사용량을 추적해 한도에 가까워지면 다음 날로 대기열을 넘기고 경고한다.
- 예약 발행이 가능한 경우에는 **플랫폼의 예약 기능(publishAt)을 우선 사용**한다. 업로드는 승인 즉시 하고, 공개는 플랫폼이 예약 시간에 처리한다.

**기타 SNS (v2, 어댑터로 추가)**
- 대상 후보: Facebook 페이지 / Instagram Reels(Meta Graph API), TikTok(Content Posting API — 앱 심사 필요), 카카오(대상 서비스와 API 제공 여부 확인 필요 — 14장 참고)
- 플랫폼별로 다른 제약을 어댑터가 처리한다(세로형 9:16 리프레이밍, 길이 제한, 예약 기능이 없는 플랫폼은 자체 스케줄러로 예약 시각에 업로드).

### 4.10 [10] 업로드 상태 확인 및 재시도
- 업로드한 뒤 상태를 폴링한다(`videos.list?part=status,processingDetails`, 1분 → 최대 10분 간격으로 점진 증가).
  - `uploadStatus`: uploaded/processed/failed/rejected
  - `processingDetails.processingStatus`, `rejectionReason`(저작권, 중복 등)
- 재시도 정책:
  | 오류 유형 | 처리 |
  |---|---|
  | 네트워크 오류, 5xx | resumable 세션으로 이어서 업로드, 지수 백오프(1, 2, 4, 8, 16분), 최대 5회 |
  | 401 (토큰 만료) | 토큰 갱신 후 즉시 재시도. 갱신이 실패하면 관리자에게 재연동 알림 |
  | 403 quotaExceeded | 할당량이 초기화되는 시각(태평양 시간 자정) 뒤로 재예약 |
  | 400 (메타데이터 오류) | 재시도하지 않음 → UPLOAD_FAILED, 검수자가 수정 후 재시도 |
  | 플랫폼 처리 실패/거부 | UPLOAD_FAILED + 사유 표시, 수동 재업로드 |
  | 썸네일 업로드만 실패 | 영상은 유지하고 썸네일만 재시도 |
- 수동 조작: 재시도, 업로드 취소(예약 영상 삭제), 메타데이터 수정 후 원격 반영(`videos.update`)
- 알림: 실패 또는 최종 공개 시 웹 화면 알림. 선택적으로 Slack/메일 Webhook
- 업로드 대시보드: 채널별 오늘 예약/공개/실패 건수, 할당량 사용률

---

## 5. 시스템 아키텍처

```
 [Browser: React SPA]
      │  REST + SSE(진행률)          tus 업로드
      ▼                                ▼
 [FastAPI API 서버] ───────────── [tusd 또는 tus 엔드포인트]
      │                                │
      ├── PostgreSQL (메타데이터, 상태, 감사로그)
      ├── Redis (Celery 브로커, 락, 진행률 pub/sub)
      └── Storage (로컬 디스크/NAS, S3 호환 MinIO로 교체 가능)
               ▲
 [Celery Workers]
   ├─ media 큐   : FFmpeg 변환/분리/렌더, 장면·무음 분석, 썸네일 (CPU 바운드)
   ├─ ai 큐      : STT API, Claude API (I/O 바운드)
   ├─ publish 큐 : 업로드, 상태 폴링
   └─ beat       : 예약 작업, 상태 폴링 주기 실행
```

### 5.1 기술 스택
| 영역 | 선택 |
|---|---|
| 백엔드 | Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2 |
| 작업 큐 | Celery 5 + Redis |
| DB | PostgreSQL 16 |
| 미디어 | FFmpeg 7, ffprobe, PySceneDetect, OpenCV(썸네일 점수), Pillow(오버레이) |
| AI | STT: 클라우드 API (Provider 추상화), LLM: Claude API (Anthropic SDK) |
| 업로드 | YouTube Data API v3 (google-api-python-client) |
| 프론트엔드 | React 18 + TypeScript + Vite, TanStack Query, video.js 또는 HTML5 video + 커스텀 타임라인, tus-js-client |
| 배포 | Docker Compose (api, worker-media, worker-ai, worker-publish, beat, postgres, redis, web) |
| 테스트 | pytest, Playwright(E2E) |

### 5.2 저장소 구조
```
storage/
  jobs/{job_id}/
    source/original.{ext}
    proxy/proxy.mp4, sprite.jpg, waveform.json
    audio/stt.flac
    analysis/scenes.json, silences.json, transcript.json, segmentation.json
    clips/{clip_id}/final.mp4, thumb_candidates/*.jpg, thumbnail.jpg, captions.srt
```
- 보존 정책: 원본은 30일, 프록시·분석 결과는 90일, 최종 클립은 업로드 성공 후 30일 (설정값)

---

## 6. 데이터 모델 (주요 엔티티)

| 엔티티 | 주요 필드 |
|---|---|
| User | id, email, name, role, password_hash |
| ProgramProfile | id, name, segmentation_rules(JSON), title_template, description_footer, default_tags, thumbnail_template(JSON), stt_vocabulary, default_channels |
| Channel | id, platform(youtube…), name, remote_channel_id, oauth_token(encrypted), quota_used_today |
| Job | id, program_id, broadcast_at, status, current_step, error, source_path, source_hash, duration, fps, width, height, created_by |
| JobStep | id, job_id, step, status, started_at, finished_at, attempts, error, output_ref |
| Transcript | id, job_id, provider, segments(JSONB), words(JSONB) |
| Clip | id, job_id, seq, type, start_sec, end_sec, start_snap, end_snap, confidence, flags[], status, reporter_name, transcript_text, render_path, locked_by, locked_at |
| ClipMetadata | clip_id, title, title_candidates[], description, tags[], hashtags[], privacy, publish_at |
| Thumbnail | id, clip_id, frame_sec, path, score, overlay_text, selected |
| PublishTask | id, clip_id, channel_id, status, remote_id, remote_url, attempts, next_retry_at, last_error, resumable_uri, published_at |
| AuditLog | id, user_id, entity, entity_id, action, diff(JSONB), created_at |

- 시간은 모두 원본 기준 초(float, 소수점 3자리)로 저장하고, 화면에서 타임코드(HH:MM:SS:FF)로 표시한다.

---

## 7. API (주요 엔드포인트)

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/uploads` (tus) | 원본 업로드 |
| POST | `/api/jobs` | 업로드 완료 파일로 작업 생성 |
| GET | `/api/jobs`, `/api/jobs/{id}` | 작업 목록/상세 |
| GET | `/api/jobs/{id}/events` | SSE 진행률 스트림 |
| POST | `/api/jobs/{id}/steps/{step}/retry` | 특정 단계부터 재실행 |
| GET | `/api/jobs/{id}/transcript` | 전사문 |
| GET/PATCH | `/api/clips/{id}` | 클립 조회/경계·메타 수정 |
| POST | `/api/clips/{id}/split`, `/api/clips/merge` | 분할/병합 |
| POST | `/api/jobs/{id}/clips` | 클립 수동 추가 |
| POST | `/api/clips/{id}/metadata/regenerate` | 메타데이터 재생성 |
| GET/POST | `/api/clips/{id}/thumbnails` | 후보 조회/현재 프레임·업로드로 추가 |
| POST | `/api/clips/{id}/approve`, `/reject` | 승인/반려 |
| POST | `/api/clips/bulk` | 일괄 승인/예약 |
| GET | `/api/publish-tasks` | 업로드 현황 |
| POST | `/api/publish-tasks/{id}/retry`, `/cancel` | 재시도/취소 |
| GET/POST | `/api/channels`, `/api/channels/oauth/callback` | 채널 연동 |
| CRUD | `/api/programs` | 프로그램 프로필 |
| GET | `/media/...` | 프록시·썸네일 스트리밍 (HTTP Range 지원, 인증 필요) |

---

## 8. 비기능 요구사항

| 항목 | 요구사항 |
|---|---|
| 성능 | 60분 원본: 프록시 변환 ≤ 6분, STT ≤ 5분(병렬 청크), 분할+메타 생성 ≤ 3분. 클립 1건 최종 렌더 ≤ 1분(4분 클립, 8코어 기준) |
| 동시성 | 작업 2~3건을 동시에 처리. media 워커 동시 실행 수는 CPU 코어에 맞춰 설정 |
| 신뢰성 | 모든 단계가 멱등. 워커가 재시작돼도 이어서 처리(acks_late). 업로드는 resumable |
| 보안 | API 키·OAuth 토큰은 환경변수/암호화 저장, 미디어 URL 인증, 감사 로그, HTTPS |
| 데이터 | 방송 원본 오디오가 외부 STT/LLM API로 전송된다는 점에 대한 사내 보안 승인 필요(14장) |
| 관측성 | 구조화 로그(JSON), 단계별 처리 시간·실패율 메트릭, Celery Flower |
| 비용 | 작업별 STT 분 수, LLM 토큰 사용량을 기록하고 관리자 화면에 표시 |
| 브라우저 | 최신 Chrome/Edge (데스크톱) |

---

## 9. 오류 처리 원칙
- 단계 실패 → Job은 `FAILED(step)`, 이전 단계 산출물은 보존하고 해당 단계부터 재시도
- 외부 API 오류(STT/LLM/YouTube): 재시도 가능한 오류(429, 5xx, 타임아웃)는 지수 백오프로 자동 재시도
- LLM 응답이 스키마와 맞지 않으면 → 검증 오류를 포함해 1회 재요청, 그래도 실패하면 단계 실패
- 경계 탐지 결과가 비정상(클립 0개, 전체 길이 대비 커버리지 < 50%)이면 → 경고와 함께 REVIEW_READY로 넘겨 사람이 수동 분할

---

## 10. 품질 평가
- **골든셋**: 실제 방송 원본 5~10편에 사람이 경계를 라벨링한 정답 데이터
- 지표:
  - 경계 정확도: |예측 − 정답| ≤ 1초 비율, 평균 절대 오차
  - 리포트 재현율/정밀도 (IoU ≥ 0.8이면 매칭)
  - 검수 수정률: 검수에서 경계가 수정된 클립 비율, 제목이 수정된 비율
- 프롬프트나 알고리즘을 바꿀 때마다 평가 스크립트로 회귀를 확인한다

---

## 11. 우선순위
| 우선순위 | 기능 |
|---|---|
| P0 (MVP) | 1~10단계 전체 (YouTube 단일 플랫폼), 기본 검수 화면, 재시도 |
| P1 | 썸네일 텍스트 오버레이 템플릿, 일괄 예약, 분할/병합, 감사 로그, 할당량 관리, 알림 |
| P2 | YouTube 자막 업로드, 기타 SNS 어댑터(Meta/TikTok/카카오), 세로형 리프레이밍, 신뢰도 기반 자동 승인, SSO |

---

## 12. 용어
| 용어 | 의미 |
|---|---|
| 리포트 | 앵커 리드 + 기자 VCR + 클로징으로 구성된 보도 한 꼭지 |
| 앵커 리드 | 리포트 앞에서 앵커가 내용을 소개하는 멘트 |
| 단신 | 기자 리포트 없이 앵커가 화면과 함께 전달하는 짧은 뉴스 |
| 프록시 | 검수·분석용 저해상도 영상 |
| 스냅 | 경계를 장면 전환/무음 지점에 맞추는 보정 |

---

## 13. 가정
- 원본은 방송 완제품(자막·그래픽 포함)이며, 별도 편집 없이 잘라서 바로 게시할 수 있다.
- 업로드 대상 YouTube 채널은 인증된 채널이다(커스텀 썸네일, 15분 넘는 영상 업로드 가능).
- 원본 영상의 저작권·초상권 문제는 업로드 전에 검수자가 판단한다(시스템은 책임지지 않음).

## 14. 미결 사항 (확인 필요)
1. **카카오/기타 SNS 대상**: 정확히 어떤 서비스인가요? (카카오톡 채널, 카카오 뷰 등) 영상 업로드용 공개 API가 없는 서비스는 자동 업로드가 어려워 다운로드 패키지 제공으로 대체해야 할 수 있습니다. Meta/TikTok 중 무엇이 필요한지도 확인 필요.
2. **STT 공급자 최종 선택**: 한국어 뉴스 정확도·화자 분리·비용을 비교해 확정(구현 초기에 샘플 원본으로 벤치마크).
3. **외부 API 전송 보안 승인**: 방송 원본 오디오와 전사문이 외부 클라우드 API로 전송되는 것에 대한 사내 정책.
4. **YouTube 할당량 증설**: 하루 예상 업로드 수 (예: 하루 2회 방송 × 20건 = 40건 → 64,000 units 필요).
5. **원본 입수 경로**: 드래그 앤 드롭 외에 NAS 감시 폴더 등 자동 수집이 필요한가?
6. **샘플 데이터**: 개발·평가용 실제 방송 원본 2~3편 (가능하면 정답 경계 라벨 포함).
