# 배포·운영 안내

사내 서버 한 대에 Docker Compose로 띄우는 절차다. 구성과 설정 항목은 [`README.md`](../README.md), 설정 전체는 [`.env.example`](../.env.example)과 `backend/app/core/config.py`를 본다.

## 1. 서버 준비

| 항목 | 권장 |
|---|---|
| CPU / 메모리 | 8코어 / 16GB 이상 (정렬 모델이 2~3GB, FFmpeg 렌더가 CPU를 많이 쓴다) |
| 디스크 | 녹화본 크기 × 보존 기간 동안의 방송 수 + 여유. 예: HD 30분 녹화본 4GB × 하루 2편 × 30일 ≈ 240GB, 프록시·렌더는 그보다 훨씬 작다 |
| OS | Docker Engine과 Compose v2가 되는 Linux |
| 외부 접속 | `api.marsl.co.kr`(큐시트), `oauth2.googleapis.com`·`www.googleapis.com`(YouTube), `api.anthropic.com`(Claude, 쓸 때만), 처음 한 번 `huggingface.co`(정렬 모델 1.2GB) |

방화벽에서 위 주소로 나가는 HTTPS(443)를 열어야 한다. 들어오는 쪽은 웹 포트(기본 8080, HTTPS를 앞에 두면 443)만 있으면 된다.

## 2. 처음 설치

```bash
git clone https://github.com/heeseong/news-clipper.git /opt/news-clipper
cd /opt/news-clipper
cp .env.example .env
```

`.env`에서 꼭 바꿀 것:

| 설정 | 값 |
|---|---|
| `POSTGRES_PASSWORD` | 새 비밀번호 |
| `ENABLE_DEV_ENDPOINTS` | `false` (더미 작업 버튼을 숨긴다) |
| `NEWSROOM_PROVIDER`, `MARSL_API_KEY` | `marsl`, MARS-L 액세스 키 |
| `PUBLISHER` | `youtube` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | OAuth 클라이언트("TV 및 입력 제한 기기") |
| `TOKEN_ENCRYPTION_KEY` | 아래 명령으로 한 번 만들고 **바꾸지 않는다**(바꾸면 채널을 다시 연결해야 한다) |
| `LLM_PROVIDER`, `ANTHROPIC_API_KEY` | Claude를 쓸 때 `claude`와 키. 안 쓰면 `off` |
| `RETENTION_ENABLED` | 보존 기간을 정한 뒤 `true` (5장) |

```bash
docker run --rm python:3.12-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
```

`.env`에는 키가 들어 있으니 권한을 줄이고(`chmod 600 .env`) git에 넣지 않는다(`.gitignore`에 있다).

```bash
docker compose up -d --build
docker compose ps          # 모두 running(healthy), migrate는 exited (0)
```

http://서버주소:8080 이 열리면:

1. **관리 > 프로그램**: `뉴스데스크`, `뉴스투데이`를 MARS-L 프로그램 이름 그대로 추가하고, 뉴스투데이의 "전날 방송분 제외"를 `뉴스데스크`로, 방송 시각과 리포트 썸네일 템플릿(PNG)을 넣는다
2. **채널**: "Google 계정으로 연결" → 다른 기기에서 google.com/device에 코드 입력 → 업로드할 채널(브랜드 계정) 선택 → 허용. 그다음 "프로그램 기본 채널"을 체크
3. 녹화본을 하나 올려 끝까지(검수 → 승인 → 렌더 → 업로드) 돌려 본다. 첫 작업은 정렬 모델(1.2GB)을 내려받느라 정렬 단계가 몇 분 더 걸린다(`models` 볼륨에 남는다)

## 3. HTTPS

웹 컨테이너(`web`, nginx)는 HTTP만 한다. 사내 표준 방식에 맞춰 앞에 HTTPS 리버스 프록시를 둔다.

- 이미 사내 리버스 프록시·로드밸런서가 있으면 그쪽에서 `http://서버주소:8080`으로 넘기면 된다
- 같은 서버에 둘 때는 `.env`에 `WEB_BIND=127.0.0.1`을 넣어 8080을 외부에서 막고, 리버스 프록시만 443을 연다

nginx 예(인증서는 사내 CA나 공인 인증서 파일):

```nginx
server {
    listen 443 ssl;
    server_name clipper.example.local;
    ssl_certificate     /etc/ssl/clipper/fullchain.pem;
    ssl_certificate_key /etc/ssl/clipper/privkey.pem;

    client_max_body_size 100m;          # 녹화본 업로드 조각(50MB)
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_request_buffering off;    # 업로드를 디스크에 쌓지 않고 바로 넘김
        proxy_buffering off;            # 실시간 갱신(SSE)
        proxy_read_timeout 300s;
    }
}
server { listen 80; server_name clipper.example.local; return 301 https://$host$request_uri; }
```

Caddy를 쓰면(공인 도메인이면 인증서를 알아서 받는다):

```
clipper.example.com {
    request_body { max_size 100MB }
    reverse_proxy 127.0.0.1:8080 {
        flush_interval -1
    }
}
```

HTTPS 주소가 생기면 YouTube 채널을 브라우저 로그인 방식으로도 연결할 수 있다: `GOOGLE_OAUTH_REDIRECT_URI=https://주소/api/channels/oauth/callback`을 넣고 Google Cloud의 "웹 애플리케이션" OAuth 클라이언트에 같은 주소를 등록한다(기기 코드 방식만 써도 된다).

## 4. 업데이트와 되돌리기

```bash
cd /opt/news-clipper
ops/backup.sh /backup/news-clipper     # 먼저 백업
git pull
docker compose up -d --build           # migrate가 DB를 새 버전으로 올린다
docker compose ps
```

문제가 생기면 이전 커밋으로 돌아간다: `git checkout <이전 커밋>` → `docker compose up -d --build`. DB 구조가 바뀐 업데이트였다면 백업에서 DB를 되돌린다(6장). 업로드·렌더 중에 업데이트하면 진행 중인 작업이 다시 시작되므로(멈춘 업로드는 10분 뒤 이어서 올린다) 한가한 시간에 한다.

## 5. 보존 기간 정리

`RETENTION_ENABLED=true`면 매일 03:17(한국 시간)에 정리한다. 기본 기간(spec 6.2):

| 대상 | 기간 | 비고 |
|---|---|---|
| 원본 녹화본 | 30일 (`RETENTION_SOURCE_DAYS`) | 렌더를 기다리는 클립이 있으면 남긴다. 지우면 그 방송은 다시 렌더할 수 없다 |
| 작업 파일(프록시·정렬 오디오·모델 캐시) | 90일 (`RETENTION_WORK_DAYS`) | 지우면 검수 화면에서 재생할 수 없다 |
| 최종 렌더 | 업로드가 모두 게시된 뒤 30일 (`RETENTION_RENDER_DAYS`) | YouTube 영상은 그대로 |
| 작업이 되지 않은 업로드 | 48시간 (`RETENTION_UPLOAD_HOURS`) | |

**관리 > 저장 공간**에서 사용량과 "지금 정리하면 지울 목록"을 볼 수 있다(자동 정리가 꺼져 있어도). 켜기 전에 목록을 한 번 확인한다.

## 6. 백업과 복원

다시 만들 수 없는 것만 백업한다: DB(작업·큐시트·클립·검수 기록·업로드 기록·암호화된 채널 토큰), 썸네일 템플릿 이미지, `.env`(키 포함). 녹화본·렌더는 크고 원본이 따로 있으므로 넣지 않는다.

```bash
ops/backup.sh /backup/news-clipper          # 날짜별 폴더, 14일 보관(KEEP_DAYS로 조정)
crontab -e
# 30 4 * * * cd /opt/news-clipper && ops/backup.sh /backup/news-clipper >> /var/log/news-clipper-backup.log 2>&1
```

백업 폴더는 다른 디스크나 NAS에 둔다. `env` 파일에 키가 있으니 접근 권한을 제한한다.

복원(새 서버나 DB가 망가졌을 때):

```bash
cd /opt/news-clipper
cp /backup/news-clipper/<날짜>/env .env
docker compose up -d postgres
docker compose exec -T postgres dropdb -U newsclipper --if-exists newsclipper
docker compose exec -T postgres createdb -U newsclipper newsclipper
docker compose exec -T postgres pg_restore -U newsclipper -d newsclipper --no-owner < /backup/news-clipper/<날짜>/db.dump
docker compose up -d                                   # migrate가 필요한 만큼 올린다
docker compose exec -T api sh -c 'cd "$STORAGE_ROOT" && tar xzf -' < /backup/news-clipper/<날짜>/branding.tar.gz
```

(`newsclipper`는 `.env`의 `POSTGRES_USER`·`POSTGRES_DB` 값으로 바꾼다.) 복원 뒤 원본이 없는 작업은 검수 화면 재생·렌더가 되지 않는다. 채널 토큰은 같은 `TOKEN_ENCRYPTION_KEY`일 때만 쓸 수 있다.

## 7. 로그와 점검

```bash
docker compose logs -f api worker-publish     # JSON 한 줄씩
docker compose logs --since 1h worker-align
curl -s localhost:8080/readyz                 # DB·Redis 연결
```

- 단계 실패는 작업 화면에서 사유를 보고 그 단계부터 다시 실행한다
- 업로드 문제는 **게시 현황**(실패·대기·반영 실패, 할당량)과 **채널**(다시 연결 필요)에서 본다
- **관리 > 운영 지표**: 처리 시간, 자동 검출률, 경계·제목 수정률, 업로드 성공률

## 8. 운영 전 점검표

- [ ] `.env`: 비밀번호·키 설정, `ENABLE_DEV_ENDPOINTS=false`, `PUBLISHER=youtube`, `NEWSROOM_PROVIDER=marsl`
- [ ] `TOKEN_ENCRYPTION_KEY`를 백업했다
- [ ] HTTPS로만 접속된다(같은 서버 리버스 프록시면 `WEB_BIND=127.0.0.1`)
- [ ] 외부 접속(MARS-L, Google, Claude, Hugging Face)이 된다
- [ ] 프로그램 프로필(이름, 방송 시각, 전날 방송분 제외, 썸네일 템플릿, 기본 채널)
- [ ] 운영 채널 연결, YouTube 할당량·채널 업로드 한도 확인(spec 16-7)
- [ ] 백업 cron, 복원을 한 번 해 봤다
- [ ] 보존 기간 확정 후 `RETENTION_ENABLED=true`
- [ ] 녹화본 한 편을 끝까지 처리해 봤다
- [ ] 로그인은 없다(이름만 넣고 쓴다, 2026-11-21 결정). 사내망에서만 접속되게 한다
