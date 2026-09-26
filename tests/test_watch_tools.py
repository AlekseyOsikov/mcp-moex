"""Описания инструментов расписания: часть контракта для клиента-планировщика."""

import pytest

from mcp_moex.tools import watch

DESCRIPTIONS = {
    "watch_set": watch.SET_DESCRIPTION,
    "watch_stop": watch.STOP_DESCRIPTION,
    "watch_status": watch.STATUS_DESCRIPTION,
    "watch_get_report": watch.GET_REPORT_DESCRIPTION,
    "watch_run_due": watch.RUN_DUE_DESCRIPTION,
    "watch_ack": watch.ACK_DESCRIPTION,
}


@pytest.mark.parametrize("name", sorted(DESCRIPTIONS))
def test_every_description_says_the_tool_is_for_the_scheduling_client(name):
    text = DESCRIPTIONS[name]

    assert "СЛУЖЕБНЫЙ" in text
    assert "клиента-планировщика" in text
    assert "не для ответов пользователю" in text


def test_set_description_states_interval_format_bounds_and_limits():
    text = watch.SET_DESCRIPTION

    for fragment in ("15m", "1h", "1d", "5m", "7d", "от 1 до 10", "replaced"):
        assert fragment in text, fragment
    assert "watch_run_due" in text  # замеры делаются по вызову клиента, а не сами
    assert "15 минут" in text


def test_run_due_description_explains_next_due_at_and_acknowledgement():
    text = watch.RUN_DUE_DESCRIPTION

    for fragment in ("next_due_at", "watch_ack", "has_more", "report_id", "reports", "null"):
        assert fragment in text, fragment


def test_ack_description_says_it_is_idempotent_and_when_to_call():
    text = watch.ACK_DESCRIPTION

    assert "Идемпотентен" in text
    assert "после успешной отправки" in text


def test_get_report_description_says_it_has_no_side_effects():
    text = watch.GET_REPORT_DESCRIPTION

    assert "report_id = null" in text
    assert "Замеров не делает" in text
    assert "не сдвигает" in text


def test_stop_and_status_descriptions_name_their_results():
    assert "stopped = false" in watch.STOP_DESCRIPTION
    assert "active = false" in watch.STATUS_DESCRIPTION
