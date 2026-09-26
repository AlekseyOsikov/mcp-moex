"""Входные параметры и результаты режима расписания: схемы совпадают с контрактом из design.md."""

import pytest
from pydantic import TypeAdapter

from mcp_moex import models

CONTRACT_FIELDS = {
    "WatchSetResult": {
        "chat_id", "secids", "poll_interval", "report_interval", "replaced",
        "next_poll_at", "next_report_at", "first_samples",
    },
    "FirstSample": {"secid", "price", "price_unit", "quote_at"},
    "WatchStopResult": {"chat_id", "stopped"},
    "WatchStatusResult": {
        "active", "chat_id", "secids", "poll_interval", "report_interval", "started_at",
        "next_poll_at", "next_report_at", "samples_in_period", "pending_reports",
    },
    "Report": {"report_id", "chat_id", "period_start", "period_end", "text", "tickers"},
    "TickerAggregate": {
        "secid", "samples", "failed_samples", "first_price", "last_price", "change_percent",
        "min_price", "max_price", "price_unit", "last_quote_at",
    },
    "RunDueResult": {"now", "next_due_at", "polled", "reports", "has_more"},
    "PolledCounts": {"chats", "tickers", "failed"},
    "AckResult": {"acknowledged"},
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(CONTRACT_FIELDS))
def test_result_fields_match_the_contract_table(name):
    model = getattr(models, name)

    assert set(model.model_fields) == CONTRACT_FIELDS[name]


@pytest.mark.parametrize("name", sorted(CONTRACT_FIELDS))
def test_every_result_field_is_described(name):
    for field, info in getattr(models, name).model_fields.items():
        assert info.description, f"{name}.{field} без описания"


@pytest.mark.parametrize(
    "param", ["ChatIdParam", "WatchSecidsParam", "PollIntervalParam", "ReportIntervalParam", "ReportIdsParam"]
)
def test_every_input_parameter_has_a_description(param):
    schema = TypeAdapter(getattr(models, param)).json_schema()

    assert schema.get("description")


def test_input_parameter_types():
    chat = TypeAdapter(models.ChatIdParam).json_schema()
    secids = TypeAdapter(models.WatchSecidsParam).json_schema()
    ids = TypeAdapter(models.ReportIdsParam).json_schema()

    assert chat["type"] == "integer"
    assert secids["type"] == "array" and secids["items"]["type"] == "string"
    assert ids["type"] == "array" and ids["items"]["type"] == "integer"


def test_interval_descriptions_state_format_and_bounds():
    poll = TypeAdapter(models.PollIntervalParam).json_schema()["description"]
    report = TypeAdapter(models.ReportIntervalParam).json_schema()["description"]

    assert "15m" in poll and "5m" in poll and "1d" in poll
    assert "7d" in report and "poll_interval" in report


def test_inactive_status_carries_only_the_active_flag():
    assert models.WatchStatusResult(active=False).model_dump() == {"active": False}

    active = models.WatchStatusResult(active=True, chat_id=1, secids=["SBER"], pending_reports=0).model_dump()
    assert active["active"] is True and active["chat_id"] == 1
