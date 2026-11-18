# News Clipper

종합뉴스 녹화본을 큐시트·원고(MARS-L)에 맞춰 리포트 단위로 자르고, 검수를 거쳐 YouTube에 예약 업로드하는 사내 웹앱.
사용자와는 **한국어**로 대화한다.

## 먼저 읽을 것
- `spec.md` — 기능 명세(v1.0). 정렬·경계 규칙의 근거는 5.5절
- `plan.md` — 마일스톤과 진행 상태. 다음 작업은 **M0(프로젝트 기반)**
- `eval/poc/RESULTS.md` — 정렬 PoC 결과와 방송별 실패 사례

## 핵심 결정 (바꾸기 전에 사용자와 확인)
- 경계 탐지는 STT가 아니라 **원고-오디오 강제 정렬**(로컬 CPU, 오디오를 사외로 보내지 않음)
- 업로드 대상은 큐시트 형식 `R` 중 **자사 리포트만**. 단신, 헤드라인, 타이틀은 제외. 광주MBC·여수MBC 리포트도 제외
- 리포트 클립 = 리드 첫 문장(앵커 인사말 제외) ~ 다음 아이템 앵커 샷 컷
- 모든 클립은 검수·승인 후 업로드. 정렬 판정에 실패한 리포트는 자동으로 자르지 않고 검수자가 직접 지정
- 스택: Python 3.12 / FastAPI / Celery+Redis / PostgreSQL / FFmpeg, React+Vite+TS, Docker Compose
- 외부 연동(MARS-L, Claude, YouTube)은 인터페이스 + Fake 구현을 먼저 만든다

## 저장소 구조 (현재)
- `backend/app/newsroom/` — 원고 파서(`script_parser.py`), 큐시트 아이템 판별·제목 정리·출처 판별(`rundown.py`), 모델(`models.py`)
- `backend/app/align/korean_text.py` — 정렬용 한글 발음 변환(숫자, 영문, 발음 사전)
- `backend/tests/` — 단위 테스트
- `eval/tools/` — MARS-L 출력물(PDF) → API 형식 JSON 변환기(API 키를 받기 전 임시용), 파싱 검토 리포트 생성
- `eval/poc/` — 정렬 PoC 스크립트(`compute_logits.py`, `align_poc.py`). M3에서 `backend/app/align/`으로 옮긴다

## 명령
```bash
cd backend && python -m pytest -q          # 단위 테스트 (pydantic, pytest만 필요)
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
- `gh`는 GraphQL이 막혀 있다. PR 생성·병합은 REST(`gh api repos/heeseong/news-clipper/pulls ...`)로 한다

## PoC에서 배운 주의점 (구현 시 지킬 것)
- ctc-segmentation의 문장 구간은 앞뒤로 최대 0.5초 늘어나 있다. 첫·마지막 **글자 시각**을 쓴다
- 창 앞쪽에 관계없는 오디오가 길면 원고 전체가 그쪽에 몰려 정렬된다. 점수로는 안 보이고 **길이 비율**로만 드러난다 → 판정 실패 시 뒤의 컷부터 재정렬
- 첫 리포트에는 거의 항상 앵커 인사말이 있다. 와이드 샷에서 시작해 앵커 컷 뒤까지 이어지기도 한다 → 컷과 리드 사이 음절 수로 감지
- 장면 컷 검출은 임계값을 낮게(ffmpeg scene 0.1). 컷 프레임은 한 프레임짜리 블렌드일 수 있다(시작 +1프레임, 끝 -½프레임)
- 프레임 단위로 정확히 자르려면 출력 쪽에서 길이를 지정한다(`-ss A -i IN -t D`). 입력 쪽 `-to`는 한 프레임이 더 들어간다
- 숫자는 읽는 소리대로 바꾼다(고유어 수사: `8곳` → `여덟곳`). 철자와 다르게 읽는 단어는 발음 사전에 넣는다(`KIA` → `기아`)

## 작업 방식
- 커밋·푸시는 기능 브랜치에서 하고, `main`에는 PR로 합친다
- 실제 방송 데이터로 검증할 때는 정답을 먼저 정하고(블라인드) 결과를 본다. 결과를 보고 고쳤으면 그 사실을 기록한다
