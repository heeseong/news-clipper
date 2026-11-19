# News Clipper — 기능 명세 (spec.md)

> 종합뉴스 방송 녹화본을 **큐시트·원고(MARS-L)**에 맞춰 리포트 단위로 자동 분할하고, 제목·설명·썸네일을 만든 뒤 검수를 거쳐 YouTube 등 채널에 예약 업로드하는 사내 웹 애플리케이션.

- 문서 버전: v1.1 (2026-11-19)
- 변경 이력
  - v0.1: STT + LLM 기반 경계 탐지
  - v0.2: **큐시트·원고 + 원고-오디오 정렬 기반으로 전환** (STT는 보조 수단), 업로드 대상을 **리포트(R)로 한정**
  - v0.3: MARS-L 외부 API 명세 반영(3.1~3.2, 5.4). 형식(R/S)·예정 길이가 API에 없어 대안 규칙 추가
  - v1.1: MARS-L 큐시트 목록(001)·조회(002)·상세(003) API 실제 응답 반영 (3.1, 3.2, 5.4): 경로·인증 헤더 확정, `programType` 필드, 큐시트 매칭 규칙 (3.1, 5.4)
  - v1.0: 4차 블라인드 검증(11-05) 반영: 앵커 컷과 리드 사이에 말소리가 있으면 인사말로 보고 제외 (5-3)
  - v0.9: 3차 검증(11-04) 반영: 판정 실패 시 다음 컷부터 재정렬, 원고 없는 아이템 길이를 탐색 창에 반영 (5-2)
  - v0.8: 오정렬 판정에 길이 비율 추가, 원고-녹화본 불일치 감지(날짜 확인, 방송 단위 경고) (5.1, 5-3-1)
  - v0.7: 여수MBC 리포트도 업로드 제외
  - v0.6: 다른 방송사(광주MBC) 리포트는 업로드 제외 (1.4, 5.4)
  - v0.5: 2차 검증(11-02) 반영: 인사말 처리, 리드 시작이 무음에 걸릴 때 보정, 문자 단위 시각 사용, 발음 사전, 오정렬 판정 기준(5.5)
  - v0.4: 정렬 PoC 결과 반영(5.3, 5.5, 5.6). 리포트 끝은 다음 아이템의 앵커 샷 컷 기준, 컷 임계값 0.1, 프레임 단위 렌더 규칙 (eval/poc/RESULTS.md)
- 상태: 초안 (16장 미결 사항 확인 필요)

---

## 1. 배경 및 목표

### 1.1 문제
- 뉴스가 끝나면 편집자가 리포트마다 수동으로 IN/OUT을 찾아 자르고, 제목과 썸네일을 만들어 업로드한다.
- 큐시트와 원고는 뉴스룸 시스템(MARS-L)에 이미 있다. 하지만 녹화본에는 송출 로그나 타임코드가 없어서 **원고가 영상의 몇 초에 해당하는지**는 알 수 없다.

### 1.2 핵심 아이디어
- "무엇이 어떤 순서로 나오는지"는 **큐시트**, "무슨 말을 하는지"는 **원고**에 이미 있다.
- 시스템이 할 일은 **원고를 녹화본 오디오의 시간축에 맞추는 것(정렬)**이다. 받아쓰기(STT)는 필요하지 않다.

### 1.3 목표
| 목표 | 지표 |
|---|---|
| 리포트 분할 자동화 | 자동 경계의 90% 이상이 실제 경계에서 ±0.5초 이내 |
| 리포트 검출 | 큐시트상 방송된 리포트의 98% 이상 검출, 오검출 0 |
| 처리 시간 | 30분 방송 기준, 업로드 완료 후 10분 안에 검수 화면 진입 |
| 검수 부담 | 리포트 1건 검수(경계 확인, 메타 수정, 승인)를 평균 1분 이내에 |
| 안정적인 배포 | 업로드 실패 시 자동 재시도, 최종 성공률 99% 이상 |

### 1.4 범위
- **업로드 대상: 큐시트 형식 `R`(리포트) 중 자사 제작 리포트만**
- 제외: 다른 방송사(광주MBC, 여수MBC)가 만든 리포트. 우리 앵커가 리드를 읽더라도 업로드하지 않는다
- 제외: 단신(`S`), 타이틀/주요뉴스(헤드라인)/클로징/CM 등 형식이 비어 있는 아이템. 단, 이 아이템들의 원고와 길이는 **정렬의 기준점**으로 사용한다.

### 1.5 범위 밖 (v1)
- 생방송 스트림 실시간 분할
- 자막 번인, 로고 삽입 등 영상 편집 효과 (정렬된 원고로 YouTube 자막 트랙을 올리는 것은 P2)
- 검수 없는 완전 자동 업로드
- 대담·시사 프로그램

---

## 2. 사용자 및 역할

| 역할 | 권한 |
|---|---|
| 편집자 (Editor) | 녹화본 업로드, 검수·수정, 메타데이터 편집 |
| 승인자 (Publisher) | 편집자 권한 + 최종 승인, 예약 발행, 업로드 재시도·취소 |
| 관리자 (Admin) | 사용자, 채널 연동(OAuth), 뉴스룸 API 연동, 프로그램 프로필 관리 |

- 사내 소규모 운영: 동시 사용자 10명 이하, 하루 방송 수 편
- 인증: 사내 계정(ID/PW). SSO는 추후 확장

---

## 3. 입력 데이터 (MARS-L)

### 3.1 MARS-L 외부 API
출처: 「지역MBC-NRCS-B106 홈페이지 인터페이스 명세서」 V0.2 (2024.01.04)

| ID | 이름 | 메서드·경로 | 용도 |
|---|---|---|---|
| IF_MARSL_001 | 큐시트 목록 조회 | `GET /api/external/cuesheet/list?startDate=&endDate=` | 방송일 + 프로그램명으로 녹화본에 맞는 큐시트 찾기 |
| IF_MARSL_002 | 큐시트 정보 조회 | `GET /api/external/cuesheet/{cueSheetId}` | 판 수(`cueSheetVersion`) 폴링 → 변경 감지 |
| IF_MARSL_003 | 큐시트 상세(아이템) 조회 | `GET /api/external/cuesheet/{cueSheetId}/detailed-item-list` | 아이템 순서 + 원고 본문 |
| IF_MARSL_004 | 취재데스크 기사 조회 | `GET /api/external/article/list?startDate=&endDate=&page=&size=` | 기사 형식·예정 시간 보조 정보 |

- 기본 URL: `https://api.marsl.co.kr`, 경로 접두어 `/api/external` (HTTPS 필수). **확인됨(2026-11-19)**: `GET https://api.marsl.co.kr/api/external/cuesheet/list?startDate=2026-11-01&endDate=2026-11-01`
- 큐시트 조회(002): `GET https://api.marsl.co.kr/api/external/cuesheet/{cueSheetId}` **확인됨(2026-11-19)**. `data`가 객체 하나이고 필드는 목록(001)의 원소와 같다(`programType` 포함). 판 수(`cueSheetVersion`) 폴링에 쓴다
- 큐시트 상세(003): `GET https://api.marsl.co.kr/api/external/cuesheet/{cueSheetId}/detailed-item-list` **확인됨(2026-11-19)**. 실제 응답은 명세서보다 필드가 많다(아래 "003 실제 응답")
- 인증: 요청 헤더 `Mars-Secret: <지역사 액세스 키>`. **확인됨(001, 002, 003)**. 키는 환경변수(`MARSL_API_KEY`)로만 받고 로그·저장소에 남기지 않는다
- 공통 응답: `{status, success, data}`. 실패 시 `{errorCode, message, details, success:false}` — 40000(잘못된 요청), 20001(권한 없음), 20000(인증 오류), 50000(시스템 오류)

**주요 필드와 용도**

| API | 필드 | 용도 |
|---|---|---|
| 001/002 | `cueSheetId` | 큐시트 식별자 |
| 001/002 | `programName`, `programType`, `broadcastDate`, `broadcastStartTime` | 녹화본과 매칭 (`programType`: `TV` / `라디오`, 명세서에 없던 필드) |
| 001/002 | `channelCode`, `channelName` | 지역사(예: `목포` / `목포문화방송`) |
| 001/002 | `cueSheetVersion` | 판 수. **값이 바뀌면 큐시트가 수정된 것** |
| 001/002 | `anchor1Name`, `anchor2Name` | 메타데이터 |
| 003 | `cueSheetItemOrderNumber` | 방송 순서 |
| 003 | `cueSheetItemTitle`, `article.articleTitle` | 제목 |
| 003 | `article.articleContent` | **원고 본문** (줄바꿈 `\n` 포함 텍스트) |
| 003 | `article.reporterName` | 기자명, 사인오프 검증 |
| 003 | `article.memo`, `cueSheetItemContent`, `remark` | 썸네일·상단 문구, 아이템 형식 후보 (실제 값 확인 필요) |
| 004 | `ancMentCttTime`, `artclCttTime`, `artclExtTime` | 앵커 멘트·기사 예정 시간(초) |
| 004 | `articleTypeCode`, `articleTypeCodeName` | 기사 형식(예: `typ_001` 스트레이트) |

**003 실제 응답 (2026-11-01 뉴스데스크로 확인)**
- 아이템: `cueSheetItemId`, `cueSheetItemOrderNumber`(문자열 `"1"`), `cueSheetItemTitle`, `remark`(리포트는 `"리포트"`, 단신은 `null`), `cueSheetItemContent`(`null`)
- `article`에 명세서의 004(취재데스크) 필드가 들어 있다: `articleId`, `ancMentCttTime`·`artclCttTime`·`artclExtTime`(초, ext는 음수 가능), `articleTypeCode`/`articleTypeCodeName`(`typ_002` 리포트, `typ_001` 스트레이트), `originReporterName`, `channelName`·`originChannelName`, `isSameChannel`(명세서의 `sameChannel`). **004 매칭은 필요 없다**
- **타이틀·주요뉴스·구분선처럼 기사가 없는 아이템은 응답에 없다.** 번호는 첫 기사부터 1이다(출력물에서는 타이틀이 1번). 그래서 이런 아이템의 예정 길이는 알 수 없고, 정렬은 첫 아이템 앞부분 여유(300초)와 재정렬로 처리한다
- `memo`는 모든 아이템에서 `null`이었다. 출력물 왼쪽 칸의 썸네일·상단 문구는 API로 오지 않는다 → 썸네일 문구는 LLM 생성이나 검수자 입력으로 대신한다
- `articleContent`는 편집기의 줄바꿈을 그대로 담는다. 단어 중간에서 끊긴 줄(`"조\n선 수군"`)과 줄 앞 공백이 있다. 파서는 문단 안의 줄을 공백으로 이어 붙인다(정렬에는 영향 없음)
- 다른 방송사 기사는 우리 데스크가 가져와도 `isSameChannel: false`, `originChannelName: "여수"`, `originReporterName`이 원래 기자다. 제목에 `여수)`가 없을 수 있다 → 출처 판별은 이 필드를 먼저 본다

**데이터 품질 주의**
- `programName`에 공백이 붙어 온다(`"뉴스데스크 "`, 실제 응답에서도 확인) → trim 후 비교
- 실제 응답(001)에서 `broadcastDurationTime`은 `0`, `broadcastEndTime`은 방송일+시작 시각(`"2026-11-01T19:30"`)이었다 → **둘 다 쓰지 않는다**. 예정 총 길이는 아이템 예정 길이의 합으로 구한다
- 같은 날짜 목록에 라디오 큐시트도 섞여 온다(`programType: "라디오"`, 시작 시각 `00:00:00`, `cueSheetVersion: 0`) → `programType == "TV"`만 매칭 대상
- `cueSheetItemOrderNumber`가 예시는 문자열, 명세는 Integer → 둘 다 허용
- 예시의 `broadcastEndTime`(`"2023-08-17T08:40"`)이 `broadcastDate`(08-18)와 날짜가 다름 → 신뢰하지 않음
- 날짜 형식이 섞여 있음(`yyyy-MM-dd HH:mm:ss`, ISO) → 관대하게 파싱
- 필드 이름 대소문자 불일치(`updateDatetime`/`updateDateTime`, `inputrUserName`/`inputUserName`) → 별칭 허용

### 3.2 큐시트 화면과 API의 차이
큐시트 출력물(PDF)에는 있지만 API 명세에는 **없는 정보**와 그 대안:

| 큐시트 화면 | API | 대안 |
|---|---|---|
| 형식 `R`/`S` | 없음 | ① `remark`·`cueSheetItemContent`에 "리포트"가 오는지 확인 (화면 비고 칸에 "리포트"가 있음) ② 원고에 `◀리포트▶` 섹션과 사인오프가 있으면 R로 판별 ③ 004 API의 `articleTypeCode` 매칭 |
| 아이템별 예정 길이(시간·합계) | 없음 (003) | ① 004 API의 `ancMentCttTime + artclCttTime`을 제목·기자·날짜로 매칭 (003 응답에 `articleId`가 없어 직접 연결 불가) ② 원고 글자 수로 낭독 시간 추정 |
| V(송출 채널), A(오디오 소스) | 없음 | 필요 없음. 아이템 경계의 화면 컷은 영상에서 직접 검출 |
| 원고 왼쪽 칸 메모(썸네일, 상단, 수퍼) | 불명확 | `memo` 필드 또는 `articleContent` 안의 특정 표기일 가능성. 실제 응답으로 확인 필요 |

- **출처 판별 규칙** (하나라도 해당하면 다른 방송사 리포트 → 업로드 제외, 정렬 기준점으로는 사용):
  - 기사의 `isSameChannel == false` 또는 `originChannelName`이 `channelName`과 다름 (003 실제 응답에서 확인, 가장 우선)
  - 제목 앞머리가 `방송사명)` 형태: `광주)기대 뒤집은 KIA…`
  - 기자 표기의 괄호가 자사 부서가 아님: `이재원(광주)` (자사는 `(보도부)`, `(보도국)` 등)
  - 004 API로 매칭된 기사의 `sameChannel=false` 또는 `originChannelName`이 자사가 아님 (실제 API 확인 후 적용)
  - 제외할 방송사 목록(기본 `광주`, `여수`)은 프로그램 프로필에서 설정한다. 판별 결과와 근거를 저장하고 검수 화면에 보여준다
- **형식 판별 규칙** (우선순위, 003 실제 응답에서는 `remark`와 `articleTypeCodeName`이 모든 아이템에서 일치했다): `remark`/`cueSheetItemContent`의 명시적 값 → 원고 구조(`◀리포트▶` + 사인오프) → 004 매칭. 판별 결과와 근거를 저장하고, 검수 화면에서 사람이 바꿀 수 있게 한다.
- **예정 길이**는 정렬의 대략 위치 추정(5-1)에만 쓰인다. 없어도 원고 낭독 시간 추정치로 대체할 수 있으므로 필수는 아니다.

### 3.3 큐시트 화면 정보 (참고: 2026-11-18 뉴스투데이 출력물)
| 필드 | 예 | 용도 |
|---|---|---|
| 프로그램명, 방송일 | 뉴스투데이, 2026-11-18 | 녹화본과 매칭하는 키 |
| 판 수, 확정일시 | 3, 2026-11-18 06:03 | 최종판 판별 |
| No | 1~23 | 방송 순서 |
| 형식 | `R`(리포트), `S`(단신), 빈칸(타이틀·클로징 등) | **R만 업로드 대상** |
| 시간 / 합계 | 03:07 / 04:07 | 예정 길이, 누적 시각 |
| 제목 | 2028년 통합목포고 개교..기존 부지에 컨벤션센터? | 제목 기본값 |
| V | V1 / V2 / V3 | 아이템마다 V1↔V2가 교대 → **아이템 경계에서 화면 컷 발생** |
| 기자 | 김양훈 | 메타데이터, 사인오프 검증 |
| 비고 | 리포트 | 형식 판별 후보 |

### 3.4 원고 (아이템별)
| 요소 | 예 | 처리 |
|---|---|---|
| 헤더 | 제목, 기자(부서), 출고 시각, 길이 | 메타데이터 |
| 섹션 표시 | `◀앵커▶ … ◀END▶`, `◀리포트▶ … ◀END▶` | **앵커 리드 구간과 리포트 구간 구분** |
| 인터뷰 | `◀INT▶ 박준철 전남광주교육청 서부시설팀장` + 인터뷰 내용 | 화자 이름 줄은 발화 아님(제거), 내용은 정렬에 사용 |
| 스탠드업 | `◀st-up▶`, `S/U` | 표시는 제거, 내용은 정렬에 사용 |
| 연출 지시 | `(CG)`, `(화면전환)`, `*목포여자고등학교*` | 발화 아님 → 제거 |
| 기자 소개 | "김양훈 기자가 취재했습니다." | 앵커 리드의 끝 |
| 사인오프 | "mbc뉴스 김양훈입니다." | **리포트 클립의 끝 기준점** |
| 왼쪽 칸 메모 | `썸네일 …`, `상단 : …`, 수퍼 자막, 인터뷰 수퍼 | **썸네일 문구, 상단 자막 → 메타데이터 후보** |

- 원고 표기는 일정하지 않다(`st-up`/`S/U`, `mbc`/`MBC`, `R]` 접두어나 `(R)` 접미어가 붙은 제목 등). 파서는 이런 변형을 허용해야 한다.

---

## 4. 전체 파이프라인

```
[1 수집] → [2 프록시 변환] → [3 A/V 분리·신호 분석] → [4 큐시트·원고 조회·파싱]
  → [5 정렬: 대략 위치 → 문장 정렬 → 경계 보정] → [6 클립 후보 생성]
  → [7 메타·썸네일 생성] → [8 검수/승인] → [6' 최종 렌더(원본 화질)]
  → [9 업로드·예약 발행] → [10 상태 확인·재시도]
```

- 2~3단계와 4단계는 서로 의존하지 않아 **병렬로 실행**한다. 5단계는 둘 다 끝나야 시작한다.
- 6단계는 구간 정보와 프록시 미리보기만 만든다. **원본 화질 렌더는 승인 뒤(6')** 수행한다. 검수 중 경계가 바뀌어도 다시 렌더할 필요가 없다.
- 각 단계는 독립된 비동기 작업이며 멱등이다. 실패하면 해당 단계부터 다시 실행할 수 있다.

### 4.1 작업(Job) 상태
```
UPLOADING → UPLOADED → PROCESSING(2·3·4 병렬) → ALIGNING → GENERATING
→ REVIEW_READY → (클립별 진행) → DONE
     ↘ FAILED (단계 정보 + 오류 메시지, 단계별 재시도 가능)
```

### 4.2 클립(Clip) 상태
```
DRAFT → APPROVED → RENDERING → RENDERED → UPLOADING → UPLOADED
→ PROCESSING(플랫폼 처리 중) → SCHEDULED → PUBLISHED
   ↘ REJECTED (업로드 제외)    ↘ UPLOAD_FAILED (자동/수동 재시도)
```
- 큐시트에는 있지만 녹화본에서 찾지 못한 리포트는 `NOT_FOUND` 클립으로 만들어 검수자에게 보여준다(방송 중 빠진 아이템, 순서 변경 등).

---

## 5. 단계별 기능 명세

### 5.1 [1] 녹화본 수집
- **웹 드래그 앤 드롭** 업로드와 파일 선택 버튼을 제공한다.
- 형식: MP4, MOV, MXF, MKV, TS. 최대 50GB(설정값).
- **분할·재개 가능한 업로드(tus 프로토콜)**: 진행률, 남은 시간 표시, 끊기면 이어 올리기
- 업로드 폼에서 입력하는 것
  - 프로그램(프로그램 프로필에서 선택, 예: 뉴스투데이)
  - 방송일 (기본값: 오늘)
  - 입력하면 **해당 큐시트를 즉시 조회해 미리 보여준다**(판 수, 리포트 개수, 예정 총 길이). 큐시트가 없으면 경고한다.
- 업로드 후 `ffprobe`로 검증(길이, 해상도, fps, 오디오 트랙).
- **날짜 확인**: 파일 이름의 날짜(예: `CT_V…_20261104_…`)나 메타데이터의 날짜가 선택한 방송일과 다르면 업로드 단계에서 경고한다. 녹화본 길이와 큐시트 예정 총 길이가 크게 다르면(예: ±30% 초과) 경고한다.
- 같은 파일을 중복 업로드하면 경고한다(해시 비교).

### 5.2 [2] 프록시 영상 변환
- 720p, H.264, CRF 28, **GOP 1초**(웹 플레이어에서 정확한 탐색용), AAC 128kbps, `+faststart`
- 비월주사 원본은 디인터레이스(`yadif`)
- 프록시와 원본의 시간축이 **1:1로 일치**해야 한다(fps 유지, 시작 PTS 0으로 정규화)
- 함께 생성: 1초 간격 썸네일 스프라이트, 오디오 파형(JSON)

### 5.3 [3] 오디오 분리 및 신호 분석
- 정렬용 오디오: 16kHz mono WAV. 오디오 트랙이 여러 개면 프로그램 프로필 설정에 따라 선택하거나 다운믹스한다.
- 분석 신호
  - **장면 전환**: 프레임 변화 점수 기반 컷 검출 — V1↔V2 전환 컷, 앵커 샷 ↔ VCR 컷. 임계값은 낮게(ffmpeg scene 0.1) 잡는다. 0.3에서는 앵커 샷 컷 8개 중 2개를 놓쳤다(PoC)
  - 컷 지점의 프레임은 한 프레임짜리 블렌드일 수 있다. 경계 계산 시 컷 프레임 자체는 어느 클립에도 넣지 않는다
  - **무음 구간**: `silencedetect` (기본 -35dB, 0.25초 이상)
  - **앵커 샷 구간**(P1): 스튜디오 화면은 서로 매우 비슷하므로 대표 프레임 임베딩을 군집화해 앵커 샷을 판별한다. 리드 시작점 보정에 사용한다.

### 5.4 [4] 큐시트·원고 조회 및 파싱
**뉴스룸 연동 (`NewsroomProvider` 인터페이스)**
```python
class NewsroomProvider(Protocol):
    def list_rundowns(self, start: date, end: date) -> list[RundownSummary]: ...   # IF_MARSL_001
    def get_rundown(self, cue_sheet_id: int) -> RundownSummary: ...                # IF_MARSL_002 (판 수 확인)
    def get_items(self, cue_sheet_id: int) -> list[RundownItemRaw]: ...            # IF_MARSL_003 (원고 포함)
    def list_articles(self, start: date, end: date) -> Iterator[ArticleRaw]: ...   # IF_MARSL_004 (페이지 순회)
```
- 구현: `MarsLProvider`(httpx, 액세스 키 헤더, 타임아웃 10초, 429·5xx 지수 백오프 재시도) + `FakeNewsroomProvider`(API 응답 형식 그대로의 JSON 픽스처)
- 원본 응답 JSON은 그대로 저장한다(파서 개선 후 재파싱, 장애 분석용)
- **큐시트 매칭**: 방송일로 목록을 조회(`startDate=endDate=방송일`) → `programType == "TV"` → `programName`(trim)이 프로그램 프로필의 이름과 같은 것 → 여러 개면 `broadcastStartTime`이 프로필의 방송 시각(예: 뉴스데스크 19:30)에 가장 가까운 것 → 그래도 애매하면 업로드 폼에서 사용자가 고른다. `cueSheetVersion`이 0인 큐시트는 내용이 비었을 수 있으므로 경고한다
- **최종판 사용**: 정렬 직전에 `cueSheetVersion`을 다시 확인하고, 바뀌었으면 아이템을 다시 조회한다. 이전 판과의 차이(추가/삭제/순서 변경)를 기록한다
- **형식 판별**과 **예정 길이**는 3.2의 대안 규칙을 따른다. 004 매칭은 `articleTitle`(공백·특수문자 정규화) + `reporterName` + 날짜로 하고, 매칭 실패는 오류가 아니다

**원고 파서**
- 입력: 원고 원문(본문 + 왼쪽 칸 메모)
- 출력: `ParsedScript`
  ```json
  {
    "item_no": 3, "type": "R", "title": "2028년 통합목포고 개교..기존 부지에 컨벤션센터?",
    "reporter": "김양훈", "planned_duration": 187,
    "anchor_lead": [{"text": "학령인구 감소에 따라 목포지역 공립 고등학교 2곳이 통합돼 2028년 개교할 예정입니다."}, "..."],
    "report": [
      {"kind": "narration", "text": "지난 1944년 개교한 목포여자고등학교"},
      {"kind": "interview", "speaker": "박준철 전남광주교육청 서부시설팀장", "text": "현재 파일공사를 진행하고 …"},
      {"kind": "standup", "text": "목포고와 목포여고의 통합 이전에 따라 …"},
      {"kind": "signoff", "text": "mbc뉴스 김양훈입니다."}
    ],
    "notes": {"thumbnail": "가칭 통합목포고 2028년 개교 / 기존 부지에는 컨벤션센터가?", "top_caption": "기존 부지에 컨벤션이?", "supers": ["목포여자고등학교", "…"]}
  }
  ```
- 규칙
  - `◀앵커▶`부터 첫 `◀END▶`까지는 앵커 리드, `◀리포트▶`부터 다음 `◀END▶`까지는 리포트 본문
  - 섹션 표시가 없는 원고(단신 등)는 전체를 하나의 앵커 발화로 취급
  - 발화가 아닌 요소 제거: `◀INT▶ 이름` 줄, `/음성변조`, `(CG)`, `(화면전환)`, `*…*`, `S/U`·`st-up` 표시
  - 원고 줄바꿈으로 끊긴 문장은 이어 붙인 뒤 문장 단위로 다시 나눔
  - 사인오프는 정규식으로 검출: `(?i)(mbc|엠비씨)\s*뉴스\s*(\S+)입니다` (프로그램 프로필에서 설정 가능)
  - 제목 정리: `R]`, `(R)`, `[리포트]` 같은 접두어·접미어 제거. 큐시트 목록의 제목은 잘려 있으므로(`…`) 원고 헤더의 전체 제목을 우선한다
- 파싱 경고: 사인오프 없음, `◀END▶` 짝이 맞지 않음, 원고 길이(낭독 시간 추정)와 예정 길이 차이가 큼

### 5.5 [5] 정렬 (리포트 경계 결정)

**5-1. 대략적 위치 추정 (영상 신호 + 큐시트)**
- 프로그램 시작점을 찾는다. 녹화본 구조는 `[앞 뉴스(없을 수도 있음)] → 타이틀(MBC 뉴스데스크 광주·전남) → 주요뉴스 → 첫 앵커 샷(인사말 + 첫 리포트 리드)` 순서다
  - v1: 타이틀 화면을 이미지 템플릿으로 찾아 탐색 시작점으로 삼는다(앞 뉴스 구간 제외)
  - PoC에서는 시작점 검출 없이 앞 뉴스 구간부터 정렬해도 첫 리포트 리드를 정확히 찾았다. 템플릿 검출은 오정렬 방지용 안전장치다
- 큐시트 누적 시각(합계)으로 각 아이템의 예상 시작 위치를 계산한다
- 장면 전환 목록과 큐시트 아이템 순서를 **동적 계획법(DTW)**으로 맞춰 예상 위치를 보정한다. V1↔V2 교대 컷이 주요 기준이다
- 목표 오차: ±20초 이내

**5-2. 문장 정렬 (강제 정렬, STT 불필요)**
- 방송 전체의 발화 원고(R과 S 모두, 순서대로)를 문장 열로 만들고, 오디오에 **CTC segmentation** 방식으로 정렬한다
  - 음향 모델: `kresnik/wav2vec2-large-xlsr-korean`(한글 음절 단위 CTC), 로컬 CPU 실행. 4코어에서 실시간의 약 6.4배 속도(18분 → 172초)
  - 숫자·기호·영문은 읽는 소리대로 한글로 바꾼 뒤 정렬한다(예: `22조 163억 원` → `이십이조 백육십삼억 원`, `8곳` → `여덟곳`, `MBC` → `엠비씨`). 철자와 다르게 읽는 단어는 발음 사전으로 처리한다(`KIA` → `기아`). 사전은 프로그램 프로필에서 추가할 수 있다
  - 문장 시각은 첫 글자와 마지막 글자의 시각을 쓴다. 문장 단위 구간은 앞뒤로 최대 0.5초 늘어나 있다
  - 아이템 창은 앞 아이템의 마지막 문장부터 시작하고, 그 문장도 함께 정렬한다(경계 기준점)
  - 큐시트 순서대로 아이템마다 창(커서부터 (예정 길이 + 앞의 원고 없는 아이템 예정 길이) × 1.5 + 60초, 첫 아이템은 녹화 앞부분 여유 300초 추가)을 잡아 정렬하고, 커서를 아이템 끝으로 옮긴다.
  - 정렬 결과가 5-3-1 판정(`LOW_CONFIDENCE`/`DURATION_MISMATCH`)에 걸리면, 창 시작을 그 뒤의 장면 컷으로 하나씩 옮겨 다시 정렬하고 처음으로 판정을 통과한 결과를 쓴다. 창 앞쪽에 다른 오디오(앞 뉴스, 주요뉴스, 브릿지 VCR)가 길게 있으면 원고 전체가 그쪽으로 몰려 정렬되기 때문이다(11-04 실제 사례: 727자가 31초에 몰림). 주요뉴스가 리드 첫 문장을 거의 그대로 읽는 경우도 이 판정으로 걸러진다 남은 오디오가 원고보다 짧으면 그 아이템은 `NOT_FOUND`(녹화본이 먼저 끝남)
  - 문장마다 `start`, `end`, `score`(정렬 신뢰도)를 저장한다
- 리포트 클립 경계
  - **시작** = 앵커 리드 첫 문장의 시작
  - **끝** = 다음 아이템의 앵커 샷 컷(다음 아이템 리드 첫 문장 직전의 컷). 단, 그 컷이 사인오프 끝 ±3초 안에 있을 때만 쓴다. 아니면 사인오프 문장 끝 + 0.4초
  - 근거(PoC): 창의 마지막 문장인 사인오프는 끝이 뒤 오디오까지 최대 2초 늘어날 수 있다. 반면 다음 아이템의 리드 시작은 ±0.5초 안으로 안정적이다
- 원고와 실제 발화가 다른 문장(애드리브, 원고 수정 미반영, 음성변조 인터뷰)은 신뢰도가 낮게 나온다. **경계 문장(첫 문장과 마지막 문장)의 신뢰도만 기준 이상이면** 중간 문장이 틀려도 클립 경계에는 영향이 없다

**5-3. 경계 보정**
- 리드 첫 글자의 시각이 무음 구간 안에 있으면 그 무음의 끝으로 옮기고 `LEAD_ONSET_IN_SILENCE` 경고를 단다(원고 첫머리와 다르게 읽은 경우. 11-02에서 실제 발생)
- 시작: 리드 시작 기준 [-3초, +0.5초] 안에서 **가장 늦은 컷**(앵커 샷 시작) + 1프레임
- 단, 그 컷과 리드 직전 무음 사이에서 음향 모델이 음절 4개 이상을 들으면 인사말로 보고 컷을 쓰지 않는다(아래 무음 규칙 적용). 인사말은 와이드 샷에서 시작해 앵커 샷 컷 뒤까지 이어질 수 있다(11-05, 11-11). 음량만으로 판단하면 음악도 말소리로 잡히므로 음절 수로 판단한다
- 그런 컷이 없으면(앵커가 인사말을 하고 같은 샷에서 리드를 읽는 경우 등) 리드 직전 무음 구간 안(무음 끝 - 0.5초)에서 시작한다. 인사말은 클립에 넣지 않는다
- 끝: 다음 아이템 리드 시작 기준 같은 방식으로 찾은 컷 - ½프레임 (5-2). 없으면 사인오프 끝 + 0.4초
- PoC 결과: 방송 2편, 리포트 8건, 경계 16개 모두 기준과 일치 (eval/poc/RESULTS.md)

**5-3-1. 오정렬 판정**
- 아이템의 문장 점수 중앙값이 -5 미만이거나, 점수 -5 미만 문장이 30%를 넘으면 `LOW_CONFIDENCE`
- 근거: 맞는 원고는 중앙값 -2.7 ~ -3.1(-5 미만 문장 0~10%), 다른 날 원고는 -7.2 ~ -10.8(88~100%). 최저 점수는 기준으로 쓰지 않는다(맞는 원고에서도 -9.6)
- 정렬된 길이가 큐시트 예정 길이와 ±20% 넘게 다르면 `DURATION_MISMATCH`. 맞는 리포트는 0.98~1.04배, 다른 날 원고는 0.21~0.48배였다. 점수 기준만으로는 경계선(32%)이거나 놓친 경우가 있어 두 기준을 함께 쓴다
- `LOW_CONFIDENCE` 또는 `DURATION_MISMATCH`인 리포트는 자동 클립을 만들지 않고 검수자가 직접 구간을 지정한다
- **방송 단위 불일치**: 정렬된 아이템의 절반 이상이 위 플래그를 받으면 작업 전체에 "큐시트와 녹화본이 맞지 않을 수 있음" 경고를 띄우고 큐시트 재선택을 요청한다 (11-03 큐시트에 11-04 녹화본이 들어온 실제 사례)
- 경계마다 `confidence`와 근거(`snapped_to: scene|silence|align`)를 저장한다

**5-4. 검증 및 경고 플래그**
| 조건 | 플래그 |
|---|---|
| 경계 문장 정렬 신뢰도가 기준 미만 | `LOW_CONFIDENCE` |
| 클립 길이가 큐시트 예정 길이와 ±20% 이상 차이 | `DURATION_MISMATCH` |
| 해당 리포트 문장이 정렬되지 않음 | `NOT_FOUND` (방송 중 빠짐 가능성) |
| 큐시트 순서와 다른 위치에서 검출 | `ORDER_CHANGED` |
| 인접 클립과 구간이 겹침 | `OVERLAP` |
| 원고에 사인오프 없음 | `NO_SIGNOFF` |

**5-5. 보조 수단: 부분 STT (선택)**
- `LOW_CONFIDENCE`나 `NOT_FOUND` 아이템에 한해 해당 구간(±60초)만 STT로 받아쓰고, 원고와 텍스트 유사도로 매칭해 위치를 찾는다
- `STTProvider` 인터페이스로 추상화한다(로컬 Whisper 또는 클라우드 API). v1에서는 꺼 두고 PoC 결과에 따라 사용 여부를 정한다

### 5.6 [6] 영상 분할
- **6-a. 클립 후보 (자동, 검수 전)**: 큐시트 `R` 아이템마다 Clip(DRAFT)을 만들고 구간(`start`, `end`)만 저장한다. 미리보기는 프록시의 해당 구간을 재생한다.
- **6-b. 최종 렌더 (승인 후)**: 원본에서 프레임 단위로 정확하게 자른다
  - H.264 High, 원본 해상도(최대 1080p, 설정값), CRF 18, AAC 192kbps 48kHz, `+faststart`
  - 길이는 출력 쪽에서 자른다(`-ss 시작 -i 원본 -t 길이`). 입력 쪽 `-to`는 B-프레임 때문에 한 프레임이 더 들어갔다(PoC)
  - 음량 정규화 EBU R128 `loudnorm` 2-pass (기본 -14 LUFS)
  - 선택: 시작 0.2초 페이드인 / 끝 0.3초 페이드아웃
  - `ffprobe`로 길이 검증(허용 오차 ±0.1초)

### 5.7 [7] 제목·설명·썸네일 생성
원고에 이미 있는 정보를 **우선** 사용하고, LLM(Claude API)은 다듬고 보완하는 데만 쓴다.

| 항목 | 기본값 (원고·큐시트) | LLM 보완 |
|---|---|---|
| 제목 | 원고 헤더의 전체 제목(접두어 정리) | 대안 제목 2개 (YouTube 100자 제한, 권장 40~60자, 과장·낚시성 표현 금지) |
| 설명 | 앵커 리드 + 템플릿 꼬리말(기자명, 방송일, 프로그램, 채널 정보) | 리포트 본문 2~3문장 요약 |
| 태그 | 프로그램 기본 태그 + 기자명 + 지역명 | 원고 기반 키워드 10개 이내, 해시태그 3개 이내 |
| 썸네일 문구 | 원고 메모의 `썸네일` 항목 | 없을 때만 `상단` 자막이나 제목에서 15자 이내 문구 생성 |

- 사실성 가드레일: LLM 결과에 나온 고유명사·숫자가 원고에 있는지 검사하고, 없으면 경고 표시
- LLM에 보내는 데이터는 **원고 텍스트뿐**이다(오디오·영상은 외부로 보내지 않는다)

**썸네일**
- 후보 프레임: 클립 구간의 장면 전환 직후 프레임 + 균등 샘플 → 품질 점수로 상위 6개
  - 점수: 선명도(Laplacian variance), 노출, 얼굴 검출 여부·크기, **앵커 샷 감점**(리포트 본문 화면 우대)
- 썸네일 템플릿(프로그램 프로필): 문구 오버레이, 채널 로고, 폰트·색상
- 출력: 1280×720 JPG, 2MB 이하. 원본 화질 프레임을 사용한다

### 5.8 [8] 검수 화면
**작업 목록**
- 프로그램, 방송일, 큐시트 판 수, 상태, 진행률, 리포트 수(승인/검출/큐시트상 전체)

**작업 상세**
- 전체 타임라인: 스프라이트 + 파형 + 아이템 블록(R은 클립 색상, S와 기타는 회색 참고 표시) + 경고 아이콘
- **큐시트 패널**: 큐시트 순서대로 아이템 목록과 검출 상태(검출/미검출/순서 변경). 클릭하면 해당 위치로 이동

**클립 편집**
- 플레이어(프록시): 프레임 단위 이동(←/→), 1초 이동(Shift+←/→), 재생 속도 조절
- 경계 편집: IN/OUT 드래그 또는 `I`/`O` 키, 경계 주변 ±3초 반복 재생, 스냅 후보(장면 전환·무음) 마커 표시
- **원고 패널**: 정렬된 문장을 재생 위치에 맞춰 하이라이트하고, 문장을 클릭하면 이동. 신뢰도 낮은 문장은 색으로 표시
- `NOT_FOUND` 리포트는 검수자가 타임라인에서 직접 구간을 지정해 클립으로 만들 수 있다
- 메타데이터 편집: 제목(원고 제목 / LLM 대안 / 직접 입력, 글자 수 표시), 설명, 태그, 공개 설정
- 썸네일: 후보 6개 중 선택 / 현재 프레임 지정 / 이미지 업로드, 문구 편집과 오버레이 미리보기
- 발행 설정: 대상 채널, 즉시 공개 / 예약(KST) / 비공개 / 일부 공개
- 일괄 처리: 여러 클립 일괄 승인, 순차 예약(예: 10분 간격)
- 감사 로그, 편집 잠금(다른 사용자가 편집 중이면 표시)

**승인 조건**: 제목 있음, 길이 제한 이내, 썸네일 선택됨, 경고 플래그 확인됨. 승인하면 최종 렌더(6-b)가 자동으로 시작된다.

### 5.9 [9] 채널 업로드 및 예약 발행
**Publisher 어댑터**
```python
class Publisher(Protocol):
    def upload(self, clip: RenderedClip, meta: PublishMeta) -> PublishResult: ...
    def set_thumbnail(self, remote_id: str, image: Path) -> None: ...
    def get_status(self, remote_id: str) -> RemoteStatus: ...
    def update(self, remote_id: str, meta: PublishMeta) -> None: ...
    def delete(self, remote_id: str) -> None: ...
```

**YouTube (v1)**
- YouTube Data API v3 `videos.insert` **resumable upload** (청크 8MB 배수, 중단 지점에서 재개)
- 예약 발행: `privacyStatus = "private"` + `publishAt`(UTC로 변환)
- `thumbnails.set` (채널 인증 필요), `categoryId = 25`(News & Politics), `defaultLanguage = ko`
- (P2) `captions.insert`: 정렬된 원고로 만든 SRT를 자막 트랙으로 업로드. STT 자막보다 정확함
- 채널 연동: 관리자가 OAuth 2.0으로 연결, refresh token은 암호화해 저장
- **할당량**: 기본 하루 10,000 units, `videos.insert` 1회 1,600 units → **하루 약 6건**. 리포트 5건/회 × 하루 방송 수를 기준으로 증설을 신청해야 한다. 일일 사용량을 추적하고 한도에 가까워지면 다음 날로 넘기고 경고한다.

**기타 SNS (v2)**: Meta(Facebook/Instagram Reels), TikTok, 카카오(대상 확정 후). 예약 기능이 없는 플랫폼은 자체 스케줄러로 예약 시각에 업로드한다.

### 5.10 [10] 업로드 상태 확인 및 재시도
- 업로드 뒤 상태 폴링(`videos.list?part=status,processingDetails`, 1분 → 최대 10분 간격으로 점진 증가)
- 재시도 정책
  | 오류 | 처리 |
  |---|---|
  | 네트워크 오류, 5xx | resumable 세션으로 이어서 업로드, 지수 백오프(1, 2, 4, 8, 16분), 최대 5회 |
  | 401 | 토큰 갱신 후 재시도. 실패하면 관리자에게 재연동 알림 |
  | 403 quotaExceeded | 할당량 초기화 시각(태평양 시간 자정) 이후로 재예약 |
  | 400 메타데이터 오류 | 재시도 안 함 → UPLOAD_FAILED, 수정 후 수동 재시도 |
  | 플랫폼 처리 실패·거부 | UPLOAD_FAILED + 사유 표시 |
  | 썸네일만 실패 | 영상은 유지하고 썸네일만 재시도 |
- 수동 조작: 재시도, 취소(예약 영상 삭제), 메타데이터 수정 후 원격 반영(`videos.update`)
- 알림: 웹 알림, 선택적으로 Slack/메일 Webhook
- 업로드 대시보드: 채널별 오늘 예약·공개·실패 건수, 할당량 사용률

---

## 6. 시스템 아키텍처

```
 [Browser: React SPA]
      │ REST + SSE(진행률)            tus 업로드
      ▼                                 ▼
 [FastAPI API 서버] ─────────────── [tus 엔드포인트]
      │                                 │
      ├── PostgreSQL (메타데이터, 상태, 감사 로그)
      ├── Redis (Celery 브로커, 락, 진행률 pub/sub)
      ├── Storage (로컬 디스크/NAS, S3 호환 MinIO로 교체 가능)
      └── MARS-L API (큐시트·원고)
               ▲
 [Celery Workers]
   ├─ media 큐   : FFmpeg 변환·분리·렌더, 장면·무음 분석, 썸네일
   ├─ align 큐   : 원고-오디오 강제 정렬 (CPU, 모델 메모리 상주)
   ├─ ai 큐      : Claude API, 뉴스룸 API 조회
   ├─ publish 큐 : 업로드, 상태 폴링
   └─ beat       : 예약 작업, 폴링, 정리 작업
```

### 6.1 기술 스택
| 영역 | 선택 |
|---|---|
| 백엔드 | Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2 |
| 작업 큐 | Celery 5 + Redis |
| DB | PostgreSQL 16 |
| 미디어 | FFmpeg 7, PySceneDetect, OpenCV, Pillow |
| 정렬 | PyTorch(CPU) + torchaudio, 한국어 wav2vec2 CTC 모델, `ctc-segmentation` |
| LLM | Claude API (Anthropic SDK) — 제목·요약·태그 보완 |
| 업로드 | YouTube Data API v3 (google-api-python-client) |
| 프론트엔드 | React 18 + TypeScript + Vite, TanStack Query, HTML5 video + 커스텀 타임라인, tus-js-client |
| 배포 | Docker Compose |
| 테스트 | pytest, Playwright(E2E) |

### 6.2 저장소 구조
```
storage/jobs/{job_id}/
  source/original.{ext}
  proxy/proxy.mp4, sprite.jpg, waveform.json
  audio/align.wav
  newsroom/rundown.json, scripts/{item_no}.json      # 원본 응답 + 파싱 결과
  analysis/scenes.json, silences.json, alignment.json
  clips/{clip_id}/final.mp4, thumb_candidates/*.jpg, thumbnail.jpg, captions.srt
```
- 보존 기간(설정값): 원본 30일, 프록시·분석 결과 90일, 최종 클립은 업로드 성공 후 30일

---

## 7. 데이터 모델

| 엔티티 | 주요 필드 |
|---|---|
| User | id, email, name, role, password_hash |
| ProgramProfile | id, name, newsroom_program_id, audio_track, signoff_pattern, title_cleanup_rules, description_footer, default_tags, thumbnail_template(JSON), default_channels |
| Channel | id, platform, name, remote_channel_id, oauth_token(encrypted), quota_used_today |
| Job | id, program_id, broadcast_date, status, current_step, error, source_path, source_hash, duration, fps, width, height, created_by |
| JobStep | id, job_id, step, status, started_at, finished_at, attempts, error |
| Rundown | id, job_id, newsroom_rundown_id, edition(판 수), confirmed_at, fetched_at, raw(JSONB) |
| RundownItem | id, rundown_id, item_no, type(R/S/null), source(타 방송사명 또는 null), source_reason, upload(bool), title, reporter, planned_duration, cumulative, video_channel(V), audio_source(A), note, parsed_script(JSONB), parse_warnings[] |
| AlignedSentence | id, rundown_item_id, seq, section(lead/report), kind, text, start_sec, end_sec, score |
| Clip | id, job_id, rundown_item_id, start_sec, end_sec, start_snap, end_snap, confidence, flags[], status, render_path, locked_by, locked_at |
| ClipMetadata | clip_id, title, title_candidates[], description, tags[], hashtags[], thumbnail_text, privacy, publish_at |
| Thumbnail | id, clip_id, frame_sec, path, score, overlay_text, selected |
| PublishTask | id, clip_id, channel_id, status, remote_id, remote_url, attempts, next_retry_at, last_error, resumable_uri, published_at |
| AuditLog | id, user_id, entity, entity_id, action, diff(JSONB), created_at |

- 시간은 원본 기준 초(소수점 3자리)로 저장하고, 화면에는 타임코드(HH:MM:SS:FF)로 표시한다.

---

## 8. API (주요 엔드포인트)

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/uploads` (tus) | 녹화본 업로드 |
| GET | `/api/newsroom/rundowns?program=&date=` | 큐시트 미리보기 (업로드 폼) |
| POST | `/api/jobs` | 작업 생성 (업로드 파일 + 프로그램 + 방송일) |
| GET | `/api/jobs`, `/api/jobs/{id}` | 작업 목록/상세 |
| GET | `/api/jobs/{id}/events` | SSE 진행률 |
| POST | `/api/jobs/{id}/steps/{step}/retry` | 단계부터 재실행 |
| POST | `/api/jobs/{id}/rundown/refresh` | 큐시트 다시 조회 후 재정렬 |
| GET | `/api/jobs/{id}/rundown` | 큐시트 + 아이템별 검출 상태 |
| GET | `/api/clips/{id}/sentences` | 정렬된 원고 문장 |
| GET/PATCH | `/api/clips/{id}` | 클립 조회, 경계·메타 수정 |
| POST | `/api/rundown-items/{id}/clip` | 미검출 리포트를 수동 구간으로 클립 생성 |
| POST | `/api/clips/{id}/metadata/regenerate` | LLM 보완 재생성 |
| GET/POST | `/api/clips/{id}/thumbnails` | 후보 조회 / 현재 프레임·업로드로 추가 |
| POST | `/api/clips/{id}/approve`, `/reject` | 승인/반려 |
| POST | `/api/clips/bulk` | 일괄 승인/예약 |
| GET | `/api/publish-tasks` | 업로드 현황 |
| POST | `/api/publish-tasks/{id}/retry`, `/cancel` | 재시도/취소 |
| GET/POST | `/api/channels`, `/api/channels/oauth/callback` | 채널 연동 |
| CRUD | `/api/programs` | 프로그램 프로필 |
| GET | `/media/...` | 프록시·썸네일 스트리밍 (HTTP Range, 인증 필요) |

---

## 9. 비기능 요구사항

| 항목 | 요구사항 |
|---|---|
| 성능 (30분 방송) | 프록시 ≤ 3분, 신호 분석 ≤ 2분, 정렬 ≤ 3분(8코어 CPU), 메타 생성 ≤ 1분. 클립 1건 최종 렌더 ≤ 1분 |
| 동시성 | 작업 2~3건 동시 처리. 정렬 워커는 모델 메모리를 고려해 동시 실행 1~2 |
| 신뢰성 | 단계 멱등, 워커 재시작 후 이어서 처리(acks_late), 업로드 resumable |
| 보안 | **오디오·영상은 사외로 전송하지 않음**(정렬은 로컬). 외부 전송은 원고 텍스트(Claude API)와 업로드 영상(YouTube)뿐. API 키·토큰 암호화 저장, 미디어 URL 인증, HTTPS |
| 관측성 | 구조화 로그(JSON), 단계별 처리 시간·실패율, 정렬 신뢰도 분포, Celery Flower |
| 비용 | 작업별 LLM 토큰 사용량 기록 |
| 브라우저 | 최신 Chrome/Edge (데스크톱) |

---

## 10. 오류 처리 원칙
- 단계가 실패하면 Job은 `FAILED(step)`이 되고, 이전 단계 산출물은 보존한 채 해당 단계부터 재시도한다.
- 큐시트를 찾지 못하면 업로드는 받되 작업은 `WAITING_RUNDOWN` 상태로 두고, 큐시트가 등록되면 이어서 진행한다(수동 재조회 버튼 제공).
- 원고가 없는 리포트는 5-1의 대략 위치만으로 클립을 만들고 `LOW_CONFIDENCE` 플래그를 단다.
- 외부 API 오류(MARS-L, Claude, YouTube) 중 재시도할 수 있는 것(429, 5xx, 타임아웃)은 지수 백오프로 재시도한다.
- LLM 응답이 스키마와 맞지 않으면 검증 오류를 넣어 1회 재요청한다. 그래도 실패하면 원고 기반 기본값만 쓰고 진행한다(작업을 실패시키지 않음).

---

## 11. 품질 평가
- **골든셋**: 녹화본 + 큐시트 + 원고 + 사람이 표시한 리포트 경계. 첫 골든셋은 **2026-11-18 뉴스투데이**(리포트 5건)
- 지표
  - 경계 정확도: |예측 − 정답| ≤ 0.5초 비율, 평균 절대 오차 (시작·끝 별도)
  - 리포트 검출률 / 오검출 수
  - 문장 정렬 신뢰도 분포
  - 검수 수정률: 경계가 수정된 클립 비율, 제목이 수정된 비율
- 파서·정렬·보정 로직을 바꿀 때마다 평가 스크립트로 회귀를 확인한다.

---

## 12. 우선순위
| 우선순위 | 기능 |
|---|---|
| P0 (MVP) | 수집, 프록시, 신호 분석, 큐시트·원고 연동과 파싱, 정렬, 원고 기반 메타·썸네일 후보, 검수, 렌더, YouTube 업로드·예약, 재시도 |
| P1 | 앵커 샷 판별, 썸네일 문구 오버레이 템플릿, LLM 대안 제목·요약, 일괄 예약, 할당량 관리, 알림, 감사 로그 |
| P2 | 부분 STT 보조, 정렬 원고 기반 YouTube 자막, 기타 SNS 어댑터, 세로형 리프레이밍, 신뢰도 기반 자동 승인, SSO |

---

## 13. 용어
| 용어 | 의미 |
|---|---|
| 큐시트 | 방송 아이템의 순서·형식·예정 길이·기자를 정리한 진행표 (MARS-L) |
| 리포트 (R) | 앵커 리드 + 기자 리포트(내레이션, 인터뷰, 스탠드업) + 사인오프로 구성된 보도 한 꼭지. **업로드 대상** |
| 단신 (S) | 앵커가 화면과 함께 읽는 짧은 뉴스. 업로드 대상 아님 |
| 앵커 리드 | 리포트 앞에서 앵커가 내용을 소개하는 멘트 (`◀앵커▶` 구간) |
| 사인오프 | 리포트 끝의 "mbc뉴스 ○○○입니다" |
| 강제 정렬 | 이미 아는 텍스트가 오디오의 몇 초에 있는지 찾는 기술 (받아쓰기와 다름) |
| 프록시 | 검수·분석용 저해상도 영상 |

---

## 14. 가정
- 녹화본은 방송 완제품(자막·그래픽 포함)이며, 잘라서 바로 게시할 수 있다.
- 녹화본 1개에는 프로그램 1회분이 들어 있다. 앞뒤 여유 구간(이전 프로그램, 광고)이 있을 수 있다.
- 원고의 발화 내용은 실제 방송과 대부분 일치한다(애드리브나 사소한 수정은 허용).
- 업로드 대상 YouTube 채널은 인증된 채널이다(커스텀 썸네일 가능).
- 저작권·초상권(음성변조, 모자이크 등) 문제는 방송 단계에서 처리됐다고 보고, 최종 판단은 검수자가 한다.

## 15. 결정 사항 기록
| 날짜 | 결정 |
|---|---|
| 2026-11-18 | 업로드 플랫폼: YouTube 우선, 기타 SNS는 v2 어댑터 |
| 2026-11-18 | 사내 소규모 웹앱, Python(FastAPI) + React, Docker Compose |
| 2026-11-18 | 리포트 클립은 앵커 리드 포함, 모든 클립은 검수 후 업로드 |
| 2026-11-18 | STT 대신 큐시트·원고 + 원고-오디오 강제 정렬. STT는 선택적 보조 수단 |
| 2026-11-18 | 업로드 대상은 리포트(R)만. 단신·헤드라인·타이틀·클로징 제외 |
| 2026-11-18 | 다른 방송사(광주MBC, 여수MBC) 리포트는 업로드 제외 |

## 16. 미결 사항 (확인 필요)
1. **MARS-L 실제 응답 확인** (명세서는 받음, 3.1 참고)
   - 2026-11-18 뉴스투데이의 `detailed-item-list` 실제 응답 1건: 리포트 아이템의 `articleContent`에 `◀앵커▶`·`◀END▶` 표기가 그대로 오는지, 원고 왼쪽 칸 메모(썸네일·상단)가 `memo`에 오는지, `remark`/`cueSheetItemContent`에 "리포트" 같은 형식 정보가 오는지
   - ~~인증 헤더 이름과 경로 접두어~~ → 확인됨: `Mars-Secret`, `/api/external` (2026-11-19)
   - ~~`articleContent` 표기, `memo`, `remark`~~ → 확인됨(003): 표기 그대로, `memo`는 비어 있음, `remark`에 "리포트"
   - 큐시트 출력물의 아이템 `시간`이 `ancMentCttTime + artclCttTime`인지, `artclExtTime`까지 더한 값인지 (골든셋 날짜의 003 응답과 출력물을 비교해 확인)
   - 004 API(취재데스크)도 같은 헤더 이름(`Mars-Secret`)을 쓰는지 (명세서에는 `MarsL-Secret`)
   - 액세스 키 발급, 앱 서버에서 `api.marsl.co.kr`로의 네트워크 접근(IP 허용 목록 여부)
2. **녹화본 샘플**: 2026-11-18 뉴스투데이 녹화본 (정렬 PoC와 첫 골든셋에 사용)
3. **녹화본 범위**: 녹화본이 프로그램 시작부터 정확히 시작하는지, 앞뒤에 다른 방송이 붙어 있는지
4. **대상 프로그램**: 뉴스투데이 외에 어떤 프로그램(예: 저녁 종합뉴스)을 처리하는지, 하루 몇 회인지 → YouTube 할당량 증설 규모 결정
5. **제목 정책**: 원고 제목을 그대로 쓸지, 유튜브용으로 다듬은 제목을 기본으로 할지
6. **카카오/기타 SNS**: 구체적인 대상 서비스와 공개 API 제공 여부
