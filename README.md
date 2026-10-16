# GameJob Discord Notifier

게임잡에서 아래 조건에 맞는 채용공고를 매일 확인하고, 새 공고만 Discord로 전송합니다.

- 모집분야: 게임개발(클라이언트)
- 지역: 서울, 경기
- 경력: 신입, 경력무관
- 정렬: 등록일순
- 실행 시각: 매일 오전 9시 10분(KST)

## 동작 방식

1. 게임잡의 채용공고 목록 API를 POST 방식으로 조회합니다.
2. 모든 검색 결과 페이지에서 `GI_No`를 수집합니다.
3. `seen_jobs.json`에 저장된 ID와 비교합니다.
4. 새 ID만 Discord Webhook으로 전송합니다.
5. 전송에 성공한 뒤 `seen_jobs.json`을 갱신하고 저장소에 자동 커밋합니다.

첫 실행에서는 기존 공고를 모두 기준 데이터로 저장하며 Discord 알림을 보내지 않습니다.

## 새 GitHub 저장소에 올리기

GitHub에서 빈 저장소를 하나 만듭니다. README, `.gitignore`, 라이선스는 GitHub 화면에서 추가하지 않아도 됩니다.

압축을 푼 폴더에서 터미널을 열고 아래 명령을 실행합니다. `<저장소-주소>`는 GitHub에서 만든 저장소 주소로 바꿉니다.

```bash
git init
git add .
git commit -m "feat: add GameJob Discord notifier"
git branch -M main
git remote add origin <저장소-주소>
git push -u origin main
```

예시:

```bash
git remote add origin https://github.com/사용자명/gamejob-notifier.git
```

## Discord Webhook 등록

Webhook URL을 코드나 저장소에 직접 넣지 마세요.

1. GitHub 저장소에서 `Settings`를 엽니다.
2. `Secrets and variables` → `Actions`로 이동합니다.
3. `New repository secret`을 누릅니다.
4. 이름은 `DISCORD_WEBHOOK_URL`로 입력합니다.
5. 값에는 Discord에서 발급한 Webhook URL을 입력합니다.

## 첫 실행

1. GitHub 저장소의 `Actions` 탭을 엽니다.
2. 왼쪽에서 `Check GameJob`을 선택합니다.
3. `Run workflow` → `Run workflow`를 누릅니다.
4. 실행이 끝나면 `seen_jobs.json`에 현재 공고 ID가 자동으로 저장됩니다.

첫 실행은 기준 데이터만 생성하므로 Discord 메시지가 오지 않는 것이 정상입니다. 이후 새 공고가 발견되면 메시지가 전송됩니다.

## 로컬 테스트

Python 3.12를 권장합니다.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -q
python main.py --dry-run
```

`--dry-run`은 Discord 전송과 `seen_jobs.json` 변경 없이 현재 검색 결과만 출력합니다.

## 실행 시각 변경

`.github/workflows/check-jobs.yml`의 cron 값을 수정합니다. GitHub Actions cron은 UTC 기준입니다.

```yaml
- cron: "10 0 * * *"
```

위 설정은 매일 `00:10 UTC`, 한국 시간으로 오전 `09:10`입니다. GitHub Actions의 예약 실행은 서버 상황에 따라 몇 분 정도 늦어질 수 있습니다.

## 필터 변경

필터는 Python 코드와 분리되어 있습니다. `config.json`만 수정하면 됩니다.

```json
{
  "filters": {
    "duty": ["1"],
    "local": ["I000", "B000"],
    "career_stat": ["0", "2"]
  },
  "sort_order": "3",
  "page_size": 40
}
```

| 설정 | 값 | 의미 |
| --- | --- | --- |
| `duty` | `1` | 게임개발(클라이언트) |
| `local` | `I000` | 서울 |
| `local` | `B000` | 경기 |
| `career_stat` | `0` | 경력무관 |
| `career_stat` | `2` | 신입 |
| `sort_order` | `3` | 등록일순 |
| `page_size` | `40` | 한 페이지당 40건 |

조건을 제외하려면 해당 배열에서 값을 삭제하고, 추가하려면 게임잡 네트워크 요청에서 확인한 코드를 배열에 넣습니다. 각 필터 배열에는 값이 적어도 하나 있어야 합니다.

별도의 설정 파일을 시험하고 싶다면 환경 변수로 경로를 지정할 수도 있습니다.

```bash
GAMEJOB_CONFIG_PATH=config.test.json python main.py --dry-run
```

게임잡이 HTML 구조나 내부 검색 코드를 변경하면 수집기가 실패할 수 있습니다. 이 경우 GitHub Actions 실행 기록에서 오류를 확인하고 선택자 또는 필터 코드를 갱신해야 합니다.

## 잡코리아 알림

`Check JobKorea`는 매일 한국시간 23:10에 별도로 실행됩니다. GitHub 예약 실행은 지연될 수 있습니다.
게임개발자 태그 MMORPG·클라이언트, 서울·경기, 신입·1~3년·경력무관을 조회합니다.
등록일순으로 모든 페이지를 수집하며, QA·운영 등 검색 결과에 포함된 공고를 임의로 제외하지 않습니다.
조건은 `jobkorea_config.json`, 수집 코드는 `jobkorea.py`에 있습니다.

1. 저장소 **Settings → Secrets and variables → Actions → New repository secret**으로 이동합니다.
2. 이름을 `JOBKOREA_DISCORD_WEBHOOK_URL`로 입력하고 새 디스코드 채널의 웹훅 URL을 값으로 저장합니다.
3. **Actions → Check JobKorea → Run workflow**로 첫 실행을 합니다.

첫 실행은 기존 공고를 `seen_jobkorea_jobs.json`에 저장하고 알림을 보내지 않습니다.
이후 신규 ID만 알림을 보내며, 전송에 성공한 공고부터 상태를 저장합니다.
전송 도중 실패해도 저장된 상태는 Actions 마지막 단계에서 커밋됩니다.
게임잡의 `DISCORD_WEBHOOK_URL` 및 `seen_jobs.json`은 별도로 유지합니다.
두 workflow는 상태 파일을 같은 브랜치에 저장하므로 동시 실행을 직렬화합니다.
잡코리아 알림은 제목·회사·직무 키워드·근무 조건·등록·마감·링크를 표시합니다.
이미지 수집은 포함하지 않습니다.

전송·상태 저장 없이 확인하려면 `python jobkorea.py --dry-run`을 실행합니다.
목록 구조 변경이나 불완전한 페이지 응답은 오류로 처리하고 수집 결과를 상태에 반영하지 않습니다.
