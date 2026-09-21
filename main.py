from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_URL = "https://www.gamejob.co.kr"
LIST_URL = f"{BASE_URL}/Recruit/_GI_Job_List/"
SEARCH_URL = f"{BASE_URL}/Recruit/joblist?menucode=searchdetail"
STATE_PATH = Path(__file__).with_name("seen_jobs.json")
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")


@dataclass(frozen=True)
class Job:
    job_id: str
    company: str
    title: str
    career: str
    education: str
    location: str
    game_field: str
    employment_type: str
    deadline: str
    registered: str
    url: str


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("POST",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/152.0 Safari/537.36"
            ),
            "Accept": "text/html, */*; q=0.01",
            "Referer": SEARCH_URL,
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return session


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"설정 파일을 읽을 수 없습니다: {error}") from error

    filters = config.get("filters", {})
    for name in ("duty", "local", "career_stat"):
        values = filters.get(name)
        if not isinstance(values, list) or not values:
            raise RuntimeError(f"config.json의 filters.{name}은 비어 있지 않은 배열이어야 합니다.")

    if config.get("page_size", 40) not in (20, 40):
        raise RuntimeError("config.json의 page_size는 20 또는 40이어야 합니다.")
    return config


def request_payload(page: int, config: dict) -> dict[str, str]:
    filters = config["filters"]
    return {
        "isDefault": "true",
        "condition[duty]": ",".join(filters["duty"]),
        "condition[local]": ",".join(filters["local"]),
        "condition[career_stat]": ",".join(filters["career_stat"]),
        "condition[menucode]": "",
        "condition[tabcode]": "1",
        "page": str(page),
        "direct": "0",
        "order": str(config.get("sort_order", "3")),
        "pagesize": str(config.get("page_size", 40)),
        "tabcode": "1",
    }


def fetch_page(session: requests.Session, page: int, config: dict) -> str:
    response = session.post(LIST_URL, data=request_payload(page, config), timeout=30)
    response.raise_for_status()
    if "jobListWrap" not in response.text:
        raise RuntimeError("게임잡 응답에서 채용공고 목록을 찾지 못했습니다.")
    return response.text


def _text(element) -> str:
    return element.get_text(" ", strip=True) if element else ""


def parse_jobs(html: str) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[Job] = []

    for row in soup.select("table.tblList tbody tr"):
        link = row.select_one('.tit > a[href*="GI_No="]')
        if not link:
            continue

        href = link.get("href", "")
        job_id = parse_qs(urlparse(href).query).get("GI_No", [""])[0]
        if not job_id:
            continue

        info = [_text(span) for span in row.select(".tit .info span")]
        info += [""] * (5 - len(info))

        jobs.append(
            Job(
                job_id=job_id,
                company=_text(row.select_one(".company strong")),
                title=_text(link.select_one("strong")) or _text(link),
                career=info[0],
                education=info[1],
                location=info[2],
                game_field=info[3],
                employment_type=info[4],
                deadline=_text(row.select_one(".date")),
                registered=_text(row.select_one(".modifyDate")),
                url=urljoin(BASE_URL, href),
            )
        )

    return jobs


def parse_last_page(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    pages = [1]
    for element in soup.select(".pagination [data-page]"):
        value = element.get("data-page", "")
        if str(value).isdigit():
            pages.append(int(value))
    return max(pages)


def fetch_all_jobs(session: requests.Session, config: dict) -> list[Job]:
    first_html = fetch_page(session, 1, config)
    jobs = parse_jobs(first_html)
    last_page = parse_last_page(first_html)

    for page in range(2, last_page + 1):
        jobs.extend(parse_jobs(fetch_page(session, page, config)))

    unique = {job.job_id: job for job in jobs}
    if not unique:
        raise RuntimeError("공고가 0건입니다. 게임잡 필터나 HTML 구조를 확인해야 합니다.")
    return list(unique.values())


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {"initialized": False, "seen_job_ids": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"상태 파일을 읽을 수 없습니다: {error}") from error

    return {
        "initialized": bool(state.get("initialized", False)),
        "seen_job_ids": [str(value) for value in state.get("seen_job_ids", [])],
    }


def save_state(seen_ids: Iterable[str], path: Path = STATE_PATH) -> None:
    numeric_sort = lambda value: (0, int(value)) if value.isdigit() else (1, value)
    state = {
        "initialized": True,
        "seen_job_ids": sorted(set(seen_ids), key=numeric_sort),
    }
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def format_job(job: Job) -> str:
    details = " · ".join(
        value for value in (job.career, job.location, job.employment_type) if value
    )
    deadline = f"마감 {job.deadline}" if job.deadline else "마감일 미정"
    return (
        f"**{job.title}**\n"
        f"{job.company}\n"
        f"{details} · {deadline}\n"
        f"<{job.url}>"
    )


def chunk_messages(jobs: list[Job], limit: int = 1900) -> list[str]:
    header = f"🎮 **게임잡 신규 공고 {len(jobs)}건**"
    messages: list[str] = []
    current = header

    for job in jobs:
        block = "\n\n" + format_job(job)
        if len(current) + len(block) > limit:
            messages.append(current)
            current = block.lstrip()
        else:
            current += block
    messages.append(current)
    return messages


def send_discord(webhook_url: str, jobs: list[Job]) -> None:
    for message in chunk_messages(jobs):
        response = requests.post(webhook_url, json={"content": message}, timeout=30)
        response.raise_for_status()


def run(dry_run: bool = False) -> int:
    config_path = Path(os.environ.get("GAMEJOB_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    jobs = fetch_all_jobs(build_session(), load_config(config_path))
    print(f"조건에 맞는 공고 {len(jobs)}건을 찾았습니다.")

    if dry_run:
        for job in jobs:
            print(json.dumps(asdict(job), ensure_ascii=False))
        return 0

    state = load_state()
    current_ids = {job.job_id for job in jobs}
    seen_ids = set(state["seen_job_ids"])

    if not state["initialized"]:
        save_state(current_ids)
        print("첫 실행: 현재 공고를 기준 데이터로 저장했습니다. 알림은 보내지 않습니다.")
        return 0

    new_jobs = [job for job in jobs if job.job_id not in seen_ids]
    if not new_jobs:
        save_state(seen_ids | current_ids)
        print("새 공고가 없습니다.")
        return 0

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL 환경 변수가 설정되지 않았습니다.")

    send_discord(webhook_url, new_jobs)
    save_state(seen_ids | current_ids)
    print(f"Discord로 신규 공고 {len(new_jobs)}건을 전송했습니다.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="게임잡 신규 공고 Discord 알림")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discord 전송과 상태 저장 없이 수집 결과만 출력합니다.",
    )
    args = parser.parse_args()
    try:
        return run(dry_run=args.dry_run)
    except Exception as error:
        print(f"오류: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
