from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Comment
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
    image_url: str = ""


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
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


def _normalized(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", value.lower())


def _image_url(image, base_url: str) -> str:
    if not image:
        return ""
    value = next(
        (image.get(name, "").strip() for name in ("src", "data-src", "data-original") if image.get(name)),
        "",
    )
    if not value or value.startswith(("data:", "blob:")):
        return ""
    url = urljoin(base_url, value)
    rejected = ("spacer", "pixel", "loading.gif", "logo_none", "view-error", "gamejob_share")
    return "" if any(word in url.lower() for word in rejected) else url


def select_recruitment_image(html: str, base_url: str) -> str:
    """채용 본문 iframe에서 모집내용에 해당하는 이미지를 고른다."""
    soup = BeautifulSoup(html, "html.parser")
    markers = ("모집요강", "모집내용", "채용내용", "담당업무", "자격요건")

    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        if not any(marker in _normalized(str(comment)) for marker in markers):
            continue
        image = comment.find_next("img")
        url = _image_url(image, base_url)
        if url:
            return url

    # 제작사마다 주석 이름이 다르므로, 본문에 이미지가 하나뿐인 경우도 지원한다.
    images = [url for image in soup.find_all("img") if (url := _image_url(image, base_url))]
    return images[0] if len(images) == 1 else ""


def _project_terms(job: Job) -> list[str]:
    bracketed = re.findall(r"[\[【]([^\]】]+)[\]】]", job.title)
    terms = [_normalized(value) for value in bracketed]
    generic = {"신입", "경력", "경력무관", "채용", "모집", "클라이언트", "개발자", "프로그래머"}
    return [term for term in terms if len(term) >= 2 and term not in generic]


def select_project_image(detail_soup: BeautifulSoup, job: Job) -> str:
    """상단 갤러리에서 공고 제목의 프로젝트명과 일치하는 이미지만 고른다."""
    terms = _project_terms(job)
    if not terms:
        return ""

    best_score = 0
    best_url = ""
    for image in detail_soup.select("article.content__visual .swiper-slide img"):
        label = _normalized(" ".join((image.get("alt", ""), image.get("title", ""))))
        score = max((len(term) for term in terms if term in label or label in term), default=0)
        url = _image_url(image, job.url)
        if url and score > best_score:
            best_score, best_url = score, url
    return best_url


def select_company_logo(detail_soup: BeautifulSoup, base_url: str) -> str:
    image = detail_soup.select_one('.logo-img img[name="cologo"], .logo-img img')
    return _image_url(image, base_url)


def fetch_job_image(session: requests.Session, job: Job) -> str:
    """모집 이미지 → 프로젝트 이미지 → 회사 로고 순으로 URL을 반환한다."""
    try:
        response = session.get(job.url, timeout=30)
        response.raise_for_status()
        detail_soup = BeautifulSoup(response.text, "html.parser")

        iframe = detail_soup.select_one("iframe#GI_Work_Content")
        if iframe and iframe.get("src"):
            iframe_url = urljoin(job.url, iframe["src"])
            iframe_response = session.get(iframe_url, timeout=30)
            iframe_response.raise_for_status()
            if image_url := select_recruitment_image(iframe_response.text, iframe_url):
                return image_url

        return select_project_image(detail_soup, job) or select_company_logo(detail_soup, job.url)
    except requests.RequestException as error:
        print(f"공고 {job.job_id} 이미지 조회 실패: {error}", file=sys.stderr)
        return ""


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


def build_embed(job: Job) -> dict:
    category = job.game_field or "게임개발(클라이언트)"
    details = " · ".join(value for value in (job.career, job.location, job.employment_type) if value)
    embed = {
        "title": job.title[:256],
        "url": job.url,
        "description": f"**{job.company}**\n{category}",
        "color": 0x5865F2,
        "fields": [
            {"name": "근무 조건", "value": details or "정보 없음", "inline": False},
            {"name": "마감", "value": job.deadline or "미정", "inline": True},
            {"name": "등록", "value": job.registered or "정보 없음", "inline": True},
        ],
    }
    if job.image_url:
        embed["image"] = {"url": job.image_url}
    return embed


def chunk_embeds(jobs: list[Job], limit: int = 10) -> list[list[dict]]:
    return [[build_embed(job) for job in jobs[index:index + limit]] for index in range(0, len(jobs), limit)]


def send_discord(webhook_url: str, jobs: list[Job]) -> None:
    for index, embeds in enumerate(chunk_embeds(jobs)):
        payload = {"embeds": embeds}
        if index == 0:
            payload["content"] = f"🎮 **게임잡 신규 공고 {len(jobs)}건**"
        response = requests.post(webhook_url, json=payload, timeout=30)
        response.raise_for_status()


def run(dry_run: bool = False) -> int:
    config_path = Path(os.environ.get("GAMEJOB_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    session = build_session()
    jobs = fetch_all_jobs(session, load_config(config_path))
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

    new_jobs = [replace(job, image_url=fetch_job_image(session, job)) for job in new_jobs]

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
