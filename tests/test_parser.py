import json

from main import Job, chunk_messages, load_config, parse_jobs, parse_last_page, request_payload


SAMPLE_HTML = """
<div class="jobListWrap">
  <table class="tblList"><tbody>
    <tr>
      <td><div class="company"><strong>에이블게임즈</strong></div></td>
      <td><div class="tit">
        <a href="/Recruit/GI_Read/View?GI_No=284106">
          <strong>[메이플키우기] 클라이언트 프로그래머 모집</strong>
        </a>
        <p class="info">
          <span>경력무관</span><span>학력무관</span>
          <span>서울 &gt; 강남구</span><span>모바일게임</span><span>정규직</span>
        </p>
      </div></td>
      <td><span class="date">채용시</span><span class="modifyDate">08/24 등록</span></td>
    </tr>
  </tbody></table>
  <div class="pagination"><span data-page="1">1</span><a data-page="2">2</a></div>
</div>
"""


def test_parse_jobs():
    jobs = parse_jobs(SAMPLE_HTML)
    assert len(jobs) == 1
    assert jobs[0].job_id == "284106"
    assert jobs[0].company == "에이블게임즈"
    assert jobs[0].career == "경력무관"
    assert jobs[0].location == "서울 > 강남구"
    assert jobs[0].url.endswith("GI_No=284106")


def test_parse_last_page():
    assert parse_last_page(SAMPLE_HTML) == 2


def test_filter_payload_uses_registration_order():
    config = {
        "filters": {
            "duty": ["1"],
            "local": ["I000", "B000"],
            "career_stat": ["0", "2"],
        },
        "sort_order": "3",
        "page_size": 40,
    }
    payload = request_payload(3, config)
    assert payload["condition[duty]"] == "1"
    assert payload["condition[local]"] == "I000,B000"
    assert payload["condition[career_stat]"] == "0,2"
    assert payload["order"] == "3"
    assert payload["page"] == "3"


def test_load_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "filters": {
                    "duty": ["1"],
                    "local": ["I000", "B000"],
                    "career_stat": ["0", "2"],
                },
                "sort_order": "3",
                "page_size": 40,
            }
        ),
        encoding="utf-8",
    )
    assert load_config(path)["filters"]["local"] == ["I000", "B000"]


def test_discord_messages_respect_length_limit():
    jobs = [
        Job(
            job_id=str(index),
            company="회사",
            title="긴 공고 제목" * 10,
            career="신입",
            education="학력무관",
            location="서울",
            game_field="모바일게임",
            employment_type="정규직",
            deadline="채용시",
            registered="방금 등록",
            url=f"https://example.com/{index}",
        )
        for index in range(20)
    ]
    messages = chunk_messages(jobs, limit=500)
    assert len(messages) > 1
    assert all(len(message) <= 500 for message in messages)
