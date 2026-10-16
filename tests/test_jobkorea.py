import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import jobkorea as jk

HTML = Path(__file__).with_name('fixtures').joinpath('jobkorea.html').read_text()
CONFIG = json.loads(jk.CONFIG_PATH.read_text())


def test_parse_real_rows():
    jobs, total, page, size = jk.parse_page(HTML)
    assert (total, page, size) == (2, 1, 40)
    assert jobs[0].job_id == '48921028'
    assert jobs[0].company == 'NC'
    assert jobs[1].career == '경력1년↑'
    assert jobs[0].deadline == '상시채용'
    assert jobs[0].url.endswith('/Recruit/GI_Read/48921028')


def test_filters_and_page():
    p = jk.request_payload(2, CONFIG)
    assert p['page'] == 2
    assert p['condition[dkwrd]'] == '10002402988,100024047745518'
    assert p['condition[career]'] == '1,2,8'
    assert p['order'] == '2'


def test_reject_blocked_or_partial_html():
    with pytest.raises(RuntimeError):
        jk.parse_page('<html>Access denied</html>')
    with pytest.raises(RuntimeError):
        jk.parse_page(HTML.replace('devloopArea', 'changed'))


def test_all_pages_and_repeated_page(monkeypatch):
    jobs, *_ = jk.parse_page(HTML)
    session = Mock()
    monkeypatch.setattr(jk.time, 'sleep', lambda _: None)
    parse = Mock(side_effect=[([jobs[0]], 2, 1, 1), ([jobs[1]], 2, 2, 1)])
    monkeypatch.setattr(jk, 'parse_page', parse)
    assert len(jk.fetch_all_jobs(session, CONFIG)) == 2
    assert session.post.call_args_list[1].kwargs['data']['page'] == 2
    parse.side_effect = [([jobs[0]], 2, 1, 1), ([jobs[0]], 2, 1, 1)]
    with pytest.raises(RuntimeError):
        jk.fetch_all_jobs(session, CONFIG)


def test_initialization_and_checkpoint_on_failure(monkeypatch, tmp_path):
    path = tmp_path / 'state.json'
    monkeypatch.setattr(jk, 'STATE_PATH', path)
    monkeypatch.setenv('JOBKOREA_DISCORD_WEBHOOK_URL', 'https://example.invalid/hook')
    monkeypatch.setattr(jk.time, 'sleep', lambda _: None)
    jobs, *_ = jk.parse_page(HTML)
    monkeypatch.setattr(jk, 'fetch_all_jobs', lambda *_: jobs)
    post = Mock()
    monkeypatch.setattr(jk.requests, 'post', post)
    jk.run()
    post.assert_not_called()
    assert len(jk.load_state(path)['seen_job_ids']) == 2
    jk.save_state([], path)
    post.side_effect = [Mock(ok=True), Mock(ok=False, status_code=500)]
    with pytest.raises(RuntimeError, match='HTTP 500'):
        jk.run()
    assert jk.load_state(path)['seen_job_ids'] == [jobs[0].job_id]
