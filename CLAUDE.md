# News Clipper

종합뉴스 녹화본을 큐시트·원고(MARS-L)에 맞춰 리포트 단위로 자르고, 검수를 거쳐 YouTube에 예약 업로드하는 사내 웹앱(목포MBC 뉴스데스크).
사용자와는 **한국어**로 대화한다.

## 현재 상태 (2026-11-19)
- 정렬 방식은 PoC로 검증 완료: 방송 4편, 리포트 17건, 경계 34개 모두 정답과 일치. 실제 MARS-L API 원고로도 같은 결과
- MARS-L API(001·002·003)는 실제 응답으로 형식 확인 완료
- 구현된 코드는 원고 파서·아이템 판별(`backend/app/newsroom/`)과 발음 변환(`backend/app/align/korean_text.py`)뿐이다. 웹앱 골격(FastAPI, DB, Celery, 프론트엔드)은 아직 없다
- **다음 작업: `plan.md`의 M0(프로젝트 기반)**

## 먼저 읽을 것
- `plan.md` — 마일스톤, 진행 상태, 다음 작업
- `spec.md` — 기능 명세(v1.2). MARS-L API는 3장, 정렬·경계 규칙은 5.5절, 데이터 모델은 7장
- `eval/poc/RESULTS.md` — 정렬 PoC 결과와 방송별 실패 사례

## 핵심 결정 (바꾸기 전에 사용자와 확인)
- 경계 탐지는 STT가 아니라 **원고-오디오 강제 정렬**(로컬 CPU, 오디오를 사외로 보내지 않음)
- 업로드 대상은 리포트(R) 중 **자사 리포트만**. 단신·타이틀·주요뉴스는 제외. 광주MBC·여수MBC 기사도 제외
- 리포트 클립 = 리드 첫 문장(앵커 인사말 제외) ~ 다음 아이템 앵커 샷 컷
- 모든 클립은 검수·승인 후 업로드. 정렬 판정에 실패한 리포트는 자동으로 자르지 않고 검수자가 직접 지정
- MARS-L은 001(목록)·002(조회)·003(상세)만 쓴다. 004는 필요 없다
- 썸네일 문구는 API에 없다(`memo`가 비어 있음) → LLM 후보 + 검수자 입력
- 스택: Python 3.12 / FastAPI / Celery+Redis / PostgreSQL / FFmpeg, React+Vite+TS, Docker Compose
- 외부 연동(MARS-L, Claude, YouTube)은 인터페이스 + Fake 구현을 먼저 만든다

## MARS-L API 요약 (자세한 내용은 spec 3.1)
- `https://api.marsl.co.kr/api/external/...`, 헤더 `Mars-Secret: <키>`. 키는 환경변수 `MARSL_API_KEY`로만 받는다
- 001 `cuesheet/list?startDate=&endDate=` → `programType == "TV"`, `programName`은 trim(뒤 공백 있음)
- 002 `cuesheet/{id}` → 판 수(`cueSheetVersion`) 확인
- 003 `cuesheet/{id}/detailed-item-list` → 원고 `article.articleContent`, 형식 `remark`·`articleTypeCodeName`, 예정 길이 `ancMentCttTime + artclCttTime + artclExtTime`, 출처 `isSameChannel`·`originChannelName`
- 기사 없는 아이템(타이틀, 주요뉴스, 브릿지)은 003에 없다. 번호는 첫 기사부터 1

## 저장소 구조 (현재)
- `backend/app/newsroom/` — 원고 파서(`script_parser.py`), 아이템 판별·제목 정리·출처 판별·예정 길이(`rundown.py`), 모델(`models.py`)
- `backend/app/align/korean_text.py` — 정렬용 한글 발음 변환(숫자, 영문, 발음 사전)
- `backend/tests/` — 단위 테스트 45개
- `eval/tools/` — MARS-L 출력물(PDF) → API 형식 JSON 변환기(실제 API가 생겨 이제 보조용), 파싱 검토 리포트 생성
- `eval/poc/` — 정렬 PoC(`compute_logits.py`, `align_poc.py`)와 결과(`RESULTS.md`). M3에서 `backend/app/align/`으로 옮긴다

## 명령
```bash
cd backend && python -m pytest -q          # 단위 테스트 (pydantic, pytest만 필요)
python eval/tools/inspect_rundown.py eval/data/<날짜>-<프로그램>   # items.json(003 형식) 파싱 결과 검토
python eval/tools/marsl_pdf_export.py CUESHEET.pdf SCRIPTS.pdf eval/data/<날짜>-<프로그램>
```
정렬 PoC 실행 방법은 `eval/poc/RESULTS.md` 끝과 `eval/poc/requirements.txt`를 본다.

## 데이터 규칙
- 실제 방송 자료(녹화본, 큐시트·원고 PDF, API 응답, 정답, 클립)는 `eval/data/`에 두고 **커밋하지 않는다**(.gitignore). 세션이 바뀌면 사라지므로 필요할 때 사용자에게 다시 받는다
- 테스트 픽스처는 실제 응답 형식을 따른 **합성** 데이터로 만든다
- API 키 값은 어디에도 기록하지 않는다

## 환경 메모 (클라우드 세션)
- 클라우드 환경 **news-clipper**에서 작업한다. Hugging Face, download.pytorch.org가 허용돼 있다
- PyTorch는 CPU 휠: `uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu`
- Hugging Face 모델은 `HF_HUB_DISABLE_XET=1`로 받는다. 모델 `kresnik/wav2vec2-large-xlsr-korean`(1.2GB)
- `gh`는 GraphQL이 막혀 있다. PR 생성·병합은 REST(`gh api repos/heeseong/news-clipper/pulls ...`)로 한다
- 원격 브랜치 삭제는 프록시가 막는다. 사용자가 GitHub에서 지운다
- `main` 병합은 사용자가 요청할 때만 한다

## PoC에서 배운 주의점 (구현 시 지킬 것)
- ctc-segmentation의 문장 구간은 앞뒤로 최대 0.5초 늘어나 있다. 첫·마지막 **글자 시각**을 쓴다
- 창 앞쪽에 관계없는 오디오가 길면 원고 전체가 그쪽에 몰려 정렬된다. 점수로는 안 보이고 **길이 비율**로만 드러난다 → 판정 실패 시 뒤의 컷부터 재정렬
- 첫 리포트에는 거의 항상 앵커 인사말이 있다. 와이드 샷에서 시작해 앵커 컷 뒤까지 이어지기도 한다 → 컷과 리드 사이 음절 수로 감지
- 주요뉴스가 첫 리포트 리드를 거의 그대로 읽기도 한다(재정렬 판정으로 걸러짐)
- 장면 컷 검출은 임계값을 낮게(ffmpeg scene 0.1). 컷 프레임은 한 프레임짜리 블렌드일 수 있다(시작 +1프레임, 끝 -½프레임)
- 프레임 단위로 정확히 자르려면 출력 쪽에서 길이를 지정한다(`-ss A -i IN -t D`). 입력 쪽 `-to`는 한 프레임이 더 들어간다
- 숫자는 읽는 소리대로 바꾼다(고유어 수사: `8곳` → `여덟곳`). 철자와 다르게 읽는 단어는 발음 사전에 넣는다(`KIA` → `기아`)

## 작업 방식
- 커밋·푸시는 기능 브랜치에서 하고, `main`에는 PR로 합친다
- 실제 방송 데이터로 검증할 때는 정답을 먼저 정하고(블라인드) 결과를 본다. 결과를 보고 고쳤으면 그 사실을 기록한다
