"""JobKorea filtered listings -> a separate Discord channel."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from main import Job, build_embed, build_session, load_state, save_state

BASE_URL = 'https://www.jobkorea.co.kr'
LIST_URL = BASE_URL + '/Recruit/Home/_GI_List/'
STATE_PATH = Path(__file__).with_name('seen_jobkorea_jobs.json')
CONFIG_PATH = Path(__file__).with_name('jobkorea_config.json')


def request_payload(page: int, config: dict) -> dict:
    payload = {'isDefault': 'true', 'page': page, 'pagesize': config['page_size'],
               'order': config['sort_order'], 'direct': 0, 'tabindex': 0,
               'onePick': 0, 'confirm': 0, 'profile': 0, 'condition[menucode]': ''}
    payload.update({f'condition[{key}]': ','.join(values)
                    for key, values in config['filters'].items()})
    return payload


def parse_page(html: str) -> tuple[list[Job], int, int, int]:
    soup = BeautifulSoup(html, 'html.parser')
    # Metadata is a JSON string passed to JSON.parse in the returned HTML.
    match = re.search(r'JSON\.parse\(("(?:\\.|[^"\\])*")\)', html)
    if not match:
        raise RuntimeError('잡코리아 목록 메타데이터가 없습니다. 차단 또는 HTML 변경을 확인하세요.')
    meta = json.loads(json.loads(match.group(1)))
    total, page, size = (int(meta[key]) for key in ('TotalCount', 'Page', 'PageSize'))
    if total < 0 or page < 1 or size < 1:
        raise RuntimeError('잡코리아 페이지 정보가 올바르지 않습니다.')
    jobs = []
    text = lambda node: node.get_text(' ', strip=True) if node else ''
    for row in soup.select('tr.devloopArea[data-gno]'):
        title = row.select_one('.tplTit a[href*="/Recruit/GI_Read/"]')
        company = row.select_one('.tplCo a.link')
        if title is None or company is None:
            raise RuntimeError('잡코리아 공고 행 형식이 변경되었습니다.')
        cells = [text(node) for node in row.select('.tplTit .etc .cell')]
        if len(cells) < 4:
            raise RuntimeError('잡코리아 공고 조건을 읽을 수 없습니다.')
        jobs.append(Job(job_id=row['data-gno'], company=text(company), title=text(title),
                        career=cells[0], education=cells[1], location=cells[2],
                        employment_type=cells[3], game_field=text(row.select_one('.tplTit .dsc'))[:800],
                        deadline=text(row.select_one('.date')), registered=text(row.select_one('.time')),
                        url=f'{BASE_URL}/Recruit/GI_Read/{row["data-gno"]}'))
    expected = min(size, max(0, total - (page - 1) * size))
    if len(jobs) != expected:
        raise RuntimeError(f'잡코리아 목록 누락: {expected}건 예상, {len(jobs)}건 파싱')
    return jobs, total, page, size


def fetch_all_jobs(session: requests.Session, config: dict) -> list[Job]:
    result = {}
    page = 1
    while page <= 100:
        response = session.post(LIST_URL, data=request_payload(page, config), timeout=30)
        response.raise_for_status()
        jobs, total, actual_page, size = parse_page(response.text)
        if actual_page != page or (jobs and not any(j.job_id not in result for j in jobs)):
            raise RuntimeError('잡코리아 페이지가 반복되었습니다. 상태를 갱신하지 않습니다.')
        result.update((job.job_id, job) for job in jobs)
        if page * size >= total:
            return list(result.values())
        page += 1
        time.sleep(0.5)
    raise RuntimeError('잡코리아 최대 100페이지 초과: 수집을 중단합니다.')


def run(dry_run: bool = False) -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    with build_session() as session:
        session.headers['Referer'] = BASE_URL + '/recruit/joblist'
        jobs = fetch_all_jobs(session, config)
    print(f'잡코리아 조건에 맞는 공고 {len(jobs)}건')
    if dry_run:
        for job in jobs:
            print(json.dumps(asdict(job), ensure_ascii=False))
        return 0
    webhook = os.environ.get('JOBKOREA_DISCORD_WEBHOOK_URL', '').strip()
    if not webhook:
        raise RuntimeError('JOBKOREA_DISCORD_WEBHOOK_URL Secret을 등록하세요.')
    state = load_state(STATE_PATH)
    seen = set(state['seen_job_ids'])
    if not state['initialized']:
        save_state((job.job_id for job in jobs), STATE_PATH)
        print('첫 실행: 기존 공고를 저장했습니다. 다음 실행부터 신규 공고만 알립니다.')
        return 0
    new_jobs = [job for job in jobs if job.job_id not in seen]
    # Save after every acknowledged message so a later failure does not resend the whole batch.
    for job in new_jobs:
        response = requests.post(webhook, json={'embeds': [build_embed(job)],
                                 'allowed_mentions': {'parse': []}}, timeout=30)
        if response.status_code == 429:
            delay = min(max(float(response.json().get('retry_after', 1)), 1), 60)
            time.sleep(delay)
            response = requests.post(webhook, json={'embeds': [build_embed(job)],
                                     'allowed_mentions': {'parse': []}}, timeout=30)
        if not response.ok:
            # Do not expose the webhook token via requests' HTTPError URL.
            raise RuntimeError(f'Discord 전송 실패 (HTTP {response.status_code})')
        seen.add(job.job_id)
        save_state(seen, STATE_PATH)
        time.sleep(0.5)
    print(f'잡코리아 신규 공고 {len(new_jobs)}건 전송 완료')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='잡코리아 신규 공고 Discord 알림')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        raise SystemExit(run(args.dry_run))
    except requests.RequestException:
        print('오류: HTTP 요청 실패. 네트워크 또는 서비스 상태를 확인하세요.', file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f'오류: {error}', file=sys.stderr)
        raise SystemExit(1)
