# WatchDog Cloud

GitHub Actions에서 30분마다 웹페이지를 확인하고, 실제로 보이는 콘텐츠가 바뀌면 Telegram으로 알려주는 Cloud 버전 WatchDog입니다.

PC가 꺼져 있어도 GitHub Actions가 실행되므로 KSASF 발표 페이지, 공고 페이지, 예약 페이지 같은 사이트를 계속 감시할 수 있습니다.

## 주요 기능

- GitHub Actions에서 30분마다 자동 실행
- Playwright Chromium으로 실제 브라우저 접속
- HTML 원본 저장
- BeautifulSoup으로 실제 보이는 텍스트 추출
- script, style, hidden input, csrf token, timestamp, 조회수 변화 무시
- KSASF 같은 게시판은 게시글 번호, 제목, 링크 중심으로 비교
- 이전/현재 스크린샷 저장
- 이미지 변경률 계산
- 이전/현재 화면을 나란히 붙인 비교 이미지 생성
- 변경 영역을 빨간 박스로 표시
- Telegram 메시지와 비교 이미지 전송
- 키워드 최초 등장 감지
- `data/state.json`으로 이전 상태 저장

## 파일 구조

```text
.
├─ monitor.py
├─ monitors.yaml
├─ requirements.txt
├─ README.md
├─ data/
│  ├─ state.json
│  ├─ html/
│  └─ screenshots/
└─ .github/
   └─ workflows/
      └─ monitor.yml
```

## GitHub 저장소 준비

1. GitHub에서 새 저장소를 만듭니다.
2. 이 프로젝트 파일을 저장소에 올립니다.
3. GitHub 저장소의 `Settings`로 이동합니다.
4. `Secrets and variables` → `Actions` → `New repository secret`을 누릅니다.
5. 아래 2개를 등록합니다.

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

## Actions 활성화

1. GitHub 저장소의 `Actions` 탭으로 이동합니다.
2. 처음 사용하는 저장소라면 Actions 사용을 허용합니다.
3. `WatchDog Cloud Monitor` workflow를 선택합니다.
4. `Run workflow`를 누르면 수동 실행할 수 있습니다.
5. 이후에는 30분마다 자동 실행됩니다.

## 감시 대상 추가

`monitors.yaml`에 감시할 사이트를 추가합니다.

```yaml
- name: KSASF
  url: https://ksasf.ksa.hs.kr/?action=BD0000M&pagecode=P000000023&language=KR
  mode: board
  image_threshold: 0.5
  keywords:
    - 본선 진출팀
    - 2026 연구발표 본선 진출팀 발표
    - 발표
```

`mode` 값은 아래처럼 사용합니다.

- `board`: 게시판 목록형 사이트용입니다. 게시글 번호, 제목, 링크 중심으로 비교합니다.
- `text`: 일반 웹페이지용입니다. 화면에 보이는 텍스트 전체를 정리해서 비교합니다.

`image_threshold`는 이미지 변경률 기준입니다. `0.5`는 0.5% 이상 달라졌을 때 이미지 변경으로 봅니다.

## Telegram 알림 내용

변경이 감지되면 아래 정보를 보냅니다.

```text
🚨 페이지 변경 감지

이름: KSASF
시간: 2026-06-26 10:30:00 KST
이미지 변경률: 0.72%
URL: https://...

추가된 텍스트:
* 123 | KSASF 2026 연구발표 본선 진출팀 발표 | https://...

삭제된 텍스트:
* 122 | 본선 참가자 발표 예정 | https://...
```

비교 이미지도 함께 전송됩니다.

## 상태 저장

실행 후 GitHub Actions가 아래 파일들을 저장소에 다시 저장합니다.

- `data/state.json`
- `data/html/`
- `data/screenshots/`

이 파일들이 저장되어야 다음 실행 때 이전 상태와 비교할 수 있습니다.

## 로컬 테스트

Python 3.12 환경에서 아래 명령으로 실행할 수 있습니다.

```bash
pip install -r requirements.txt
python -m playwright install chromium
python monitor.py
```

Telegram을 실제로 보내려면 환경변수를 설정해야 합니다.

```bash
set TELEGRAM_BOT_TOKEN=your_bot_token
set TELEGRAM_CHAT_ID=your_chat_id
python monitor.py
```

Windows PowerShell에서는 아래처럼 설정합니다.

```powershell
$env:TELEGRAM_BOT_TOKEN="your_bot_token"
$env:TELEGRAM_CHAT_ID="your_chat_id"
python monitor.py
```
