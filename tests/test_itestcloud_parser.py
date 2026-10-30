from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fetchers.itestcloud import (
    map_exam_status,
    map_training_status,
    parse_exam_list,
    parse_training_list,
)

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)

# 英文界面的卡片：侧栏在 task-middle-wrap 之外
_CARD = """
<div class="test2 train_task_item">
  <div class="now-banji1 head" data-id="12345">
    <h2 class="banji-name taskName">Listening Practice</h2>
    <a id="toKs_12345" class="task-link"
       href="javascript:throttled_entryExam(12345);"
       data-time="2026-10-13 14:00:00@2026-11-01 23:59:00">Continue</a>
  </div>
  <div class="test-mid task-middle-wrap">
    <dl><dt>Total score:</dt><dd class="spec-show">100 (pass: 60)</dd></dl>
    <dl><dt>Training time:</dt><dd>2026-10-13 14:00:00 ~ 2026-11-01 23:59:00</dd></dl>
    <dl><dt>Scoring:</dt><dd>Highest score</dd></dl>
  </div>
  <div class="task-sidebar">
    <dl><dt class="task-score-dabiao"><span>87.9</span></dt><dd>Achieved</dd></dl>
  </div>
</div>
"""

# 中文界面（真实页面结构）：侧栏在 task-middle-wrap 之内；第二张卡尚未作答
_CN_HTML = """
<div class="test-infor">
 <div class="test2 train_task_item">
  <div class="now-banji1 head" data-id="12661200">
   <div><h2 class="banji-name float-left taskName">25 GE3 Listening and Reading Task 2</h2></div>
   <a href="javascript:throttled_entryExam(12661200);" id="toKs_12661200"
      data-time="2026-10-13 14:00:00@2026-11-01 23:59:00" class="banji-daing float-right task-link">再来一次</a>
  </div>
  <div class="test-mid task-middle-wrap">
   <div class="task-main">
    <dl><dt>训练总分：</dt><dd class="spec-show"> 100分
       (达标分数：60分)</dd></dl>
   </div>
   <div class="task-sidebar">
    <dl><dt class="task-score-dabiao">96.16<span>分</span></dt><dd> 已达标 </dd></dl>
   </div>
  </div>
 </div>
 <div class="test2 train_task_item">
  <div class="now-banji1 head" data-id="777">
   <div><h2 class="taskName">Open Task</h2></div>
   <a data-time="2026-10-20 08:00:00@2026-12-01 23:59:00" class="task-link">开始训练</a>
  </div>
  <div class="task-middle-wrap"><div class="task-sidebar"></div></div>
 </div>
</div>
"""

SUBMITTED_EXAM = {
    "ksId": 1000464276,
    "ksdId": 1054986535,
    "ksName": "25级软工联培2 Mid-term exam",
    "finish": 2,
    "restrictBeginTime": 1778725200000,  # 2026-05-14 10:20 CST
    "restrictEndTime": 1778730300000,    # 2026-05-14 11:45 CST
    "ansSubmitTime": 1778729553000,
    "totalScore": 100,
    "restrictBeginTimeStr": "2026-05-14 10:20:00",
    "canViewKaojuanJudgeBean": {"scoreText": "91", "kaojuanReason": "不允许查看答题"},
}

MISSED_EXAM = {
    "ksId": 1000469555,
    "ksName": "25级Mid-term补考",
    "finish": 0,
    "restrictBeginTime": 1779626700000,
    "restrictEndTime": 1779631800000,
    "ansSubmitTime": None,
    "totalScore": 100,
    "canViewKaojuanJudgeBean": {"scoreText": "", "kaojuanReason": "缺考"},
}


# ---------------------------------------------------------------------------
# 训练
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("category", "course"),
    [("class", "班级训练"), ("mock", "模考训练"), ("basic", "基础训练")],
)
def test_parse_current_training_card(category: str, course: str):
    (event,) = parse_training_list(_CARD, category, now=NOW)

    assert event.platform == "itestcloud"
    assert event.source_id == f"{category}:12345"
    assert event.course == course
    assert event.title == "Listening Practice"
    assert event.status == "submitted"
    assert event.start_at == datetime(2026, 10, 13, 6, 0, tzinfo=UTC)
    assert event.end_at == datetime(2026, 11, 1, 15, 59, tzinfo=UTC)
    assert event.raw_payload["score_raw"] == "87.9"
    assert event.raw_payload["completion_raw"] == "Achieved"
    assert event.raw_payload["start_raw"] == "2026-10-13 14:00:00"
    assert event.raw_payload["end_raw"] == "2026-11-01 23:59:00"


def test_sidebar_is_not_mixed_into_details():
    (event,) = parse_training_list(_CARD, "class", now=NOW)

    labels = [d["label"] for d in event.raw_payload["details"]]
    assert labels == ["Total score:", "Training time:", "Scoring:"]
    assert event.description.endswith("成绩 87.9（Achieved）")


def test_chinese_page_layout():
    done, open_ = parse_training_list(_CN_HTML, "class", now=NOW)

    assert done.source_id == "class:12661200"
    assert done.status == "submitted"
    assert done.raw_payload["completion_raw"] == "已达标"
    assert "成绩 96.16 分（已达标）" in done.description
    assert open_.source_id == "class:777"
    assert open_.status == "open"
    assert open_.raw_payload["score_raw"] == ""


def test_parse_empty_list():
    assert parse_training_list('<div class="nodataBox">No data</div>', "class") == []


def test_score_without_achievement_marker_remains_open():
    html = _CARD.replace("Achieved", "Not achieved")
    (event,) = parse_training_list(html, "class", now=NOW)

    assert event.status == "open"


def test_chinese_achievement_marker_maps_to_submitted():
    html = _CARD.replace("Achieved", "已达标")
    (event,) = parse_training_list(html, "class", now=NOW)

    assert event.status == "submitted"


def test_ended_training_without_pass_is_closed():
    html = _CN_HTML.replace("2026-12-01 23:59:00", "2026-10-25 23:59:00")
    _, ended = parse_training_list(html, "class", now=NOW)

    assert ended.status == "closed"


@pytest.mark.parametrize(
    ("completion", "expected"),
    [
        ("已达标", "submitted"),
        ("Completed", "submitted"),
        ("未达标", "open"),
        ("not achieved", "open"),
        ("", "open"),
        ("进行中", "unknown"),
    ],
)
def test_map_training_status(completion: str, expected: str):
    future = datetime(2026, 12, 1, tzinfo=UTC)
    assert map_training_status(completion, future, now=NOW) == expected


def test_unknown_status_is_kept_after_deadline():
    past = datetime(2026, 10, 1, tzinfo=UTC)
    assert map_training_status("进行中", past, now=NOW) == "unknown"


def test_tilde_separator_and_date_only_end():
    html = _CARD.replace("2026-10-13 14:00:00@2026-11-01 23:59:00", "2026-10-13 ~ 2026-11-01")
    (event,) = parse_training_list(html, "class", now=NOW)

    assert event.start_at == datetime(2026, 10, 12, 16, 0, tzinfo=UTC)
    assert event.end_at == datetime(2026, 11, 1, 15, 59, 59, tzinfo=UTC)


def test_skip_cards_without_stable_id_or_title():
    html = """
    <div class="train_task_item">
      <div class="now-banji1 head"><h2 class="taskName">Missing ID</h2></div>
    </div>
    <div class="train_task_item">
      <div class="now-banji1 head" data-id="9"></div>
    </div>
    """
    assert parse_training_list(html, "class") == []


def test_missing_time_keeps_task_without_deadline():
    html = _CARD.replace(' data-time="2026-10-13 14:00:00@2026-11-01 23:59:00"', "")
    (event,) = parse_training_list(html, "class", now=NOW)

    assert event.start_at is None
    assert event.end_at is None
    assert event.raw_payload["time_range_raw"] == ""


def test_invalid_category_is_rejected():
    with pytest.raises(ValueError, match="Unknown iTEST training category"):
        parse_training_list(_CARD, "exam")


# ---------------------------------------------------------------------------
# 考试
# ---------------------------------------------------------------------------
def test_submitted_exam_maps_fields():
    (ev,) = parse_exam_list([SUBMITTED_EXAM], now=NOW)

    assert ev.platform == "itestcloud"
    assert ev.source_id == "exam:1000464276"
    assert ev.course == "考试"
    assert ev.status == "submitted"
    assert ev.start_at == datetime(2026, 5, 14, 2, 20, tzinfo=UTC)
    assert ev.end_at == datetime(2026, 5, 14, 3, 45, tzinfo=UTC)
    assert "成绩 91" in ev.description
    assert ev.raw_payload["ksd_id"] == 1054986535


def test_missed_exam_is_closed():
    (ev,) = parse_exam_list([MISSED_EXAM], now=NOW)

    assert ev.status == "closed"
    assert "缺考" in ev.description


def test_future_unsubmitted_exam_is_open():
    future = {**MISSED_EXAM, "restrictEndTime": int(datetime(2026, 11, 1, tzinfo=UTC).timestamp() * 1000)}
    assert map_exam_status(future, now=NOW) == "open"


def test_exam_without_ks_id_skipped():
    assert parse_exam_list([{"ksName": "x"}], now=NOW) == []
