from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fetchers.itestcloud import parse_training_list

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


@pytest.mark.parametrize("category", ["class", "mock", "basic"])
def test_parse_current_training_card(category: str):
    (event,) = parse_training_list(_CARD, category)

    assert event.platform == "itestcloud"
    assert event.source_id == f"{category}:12345"
    assert event.title == "Listening Practice"
    assert event.status == "submitted"
    assert event.start_at == datetime(2026, 10, 13, 6, 0, tzinfo=UTC)
    assert event.end_at == datetime(2026, 11, 1, 15, 59, tzinfo=UTC)
    assert event.raw_payload["score_raw"] == "87.9"
    assert event.raw_payload["completion_raw"] == "Achieved"


def test_parse_empty_list():
    assert parse_training_list('<div class="nodataBox">No data</div>', "class") == []


def test_score_without_achievement_marker_remains_open():
    html = _CARD.replace("Achieved", "Not achieved")
    (event,) = parse_training_list(html, "class")

    assert event.status == "open"


def test_chinese_achievement_marker_maps_to_submitted():
    html = _CARD.replace("Achieved", "已达标")
    (event,) = parse_training_list(html, "class")

    assert event.status == "submitted"


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
    (event,) = parse_training_list(html, "class")

    assert event.start_at is None
    assert event.end_at is None
    assert event.raw_payload["time_range_raw"] == ""


def test_invalid_category_is_rejected():
    with pytest.raises(ValueError, match="Unknown iTEST training category"):
        parse_training_list(_CARD, "exam")
