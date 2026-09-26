"""Сервис расписания на фейковом ISS и управляемых часах: постановка, остановка, состояние."""

import sqlite3
from datetime import datetime

import pytest

from mcp_moex.errors import InvalidArguments, IssUnavailable, SecurityNotFound, WatchNotSet
from mcp_moex.tools.price import MSK

from .fake_iss import RECORD_ENV
from .watch_helpers import T0, Env

POLL = 900
HOUR = 3600


def iso(offset: int = 0) -> str:
    return datetime.fromtimestamp(T0 + offset, MSK).isoformat()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv(RECORD_ENV, raising=False)


@pytest.fixture
def env(tmp_path) -> Env:
    return Env(tmp_path)


# --- 4.1 постановка -------------------------------------------------------------------------------


async def test_first_set_returns_canonical_tickers_deadlines_and_first_samples(env):
    result = await env.service.set(1, ["sber", "TMOS"], "15m", "1h")

    assert result.secids == ["SBER", "TMOS"]
    assert result.replaced is False
    assert (result.poll_interval, result.report_interval) == ("15m", "1h")
    assert result.next_poll_at == iso(POLL)
    assert result.next_report_at == iso(HOUR)
    assert [s.secid for s in result.first_samples] == ["SBER", "TMOS"]
    assert all(s.price > 0 and s.quote_at.endswith("+03:00") for s in result.first_samples)
    status = await env.service.status(1)
    assert status.samples_in_period == 2


async def test_interval_is_returned_in_the_form_it_was_accepted(env):
    result = await env.service.set(1, ["SBER"], " 60M ", "120m")

    assert (result.poll_interval, result.report_interval) == ("60m", "120m")
    status = await env.service.status(1)
    assert (status.poll_interval, status.report_interval) == ("60m", "120m")


async def test_set_replaces_the_previous_watch_and_drops_its_samples(env):
    await env.service.set(1, ["SBER", "TMOS"], "15m", "1h")
    env.clock.advance(60)

    result = await env.service.set(1, ["IMOEX"], "5m", "30m")

    assert result.replaced is True
    status = await env.service.status(1)
    assert status.secids == ["IMOEX"]
    assert status.samples_in_period == 1  # замеры прежнего опроса не входят в новый
    assert status.next_poll_at == iso(60 + 300)


async def test_duplicate_tickers_are_dropped_keeping_order_and_polled_once(env):
    result = await env.service.set(1, ["SBER", "sber", "TMOS"], "15m", "1h")

    assert result.secids == ["SBER", "TMOS"]
    assert env.exchange.quote_requests("SBER") == 1


async def test_unknown_ticker_fails_and_keeps_the_previous_watch(env):
    await env.service.set(1, ["SBER"], "15m", "1h")
    before = await env.service.status(1)
    env.clock.advance(60)

    with pytest.raises(SecurityNotFound, match="NOPE123"):
        await env.service.set(1, ["TMOS", "NOPE123"], "5m", "1d")

    assert await env.service.status(1) == before


async def test_first_set_with_unknown_ticker_creates_nothing(env):
    with pytest.raises(SecurityNotFound):
        await env.service.set(1, ["NOPE123"], "15m", "1h")

    assert (await env.service.status(1)).active is False


async def test_exchange_outage_on_first_sample_is_an_error_and_changes_nothing(env):
    await env.service.set(1, ["SBER"], "15m", "1h")
    before = await env.service.status(1)
    env.clock.advance(60)
    env.exchange.all_down = True

    with pytest.raises(IssUnavailable):
        await env.service.set(1, ["TMOS"], "15m", "1h")

    env.exchange.all_down = False
    assert await env.service.status(1) == before


@pytest.mark.parametrize("secids", [[], [f"T{i}" for i in range(11)], ["SBER"] + [f"T{i}" for i in range(10)]])
async def test_ticker_count_limit_is_an_error_naming_the_allowed_range(env, secids):
    with pytest.raises(InvalidArguments) as error:
        await env.service.set(1, secids, "15m", "1h")

    assert "secids" in str(error.value) and "10" in str(error.value)
    assert env.exchange.requests == []


async def test_ten_tickers_are_the_limit_and_duplicates_do_not_count(env):
    with pytest.raises(InvalidArguments):
        await env.service.set(1, [f"T{i}" for i in range(11)], "15m", "1h")

    result = await env.service.set(1, ["SBER"] * 12, "15m", "1h")

    assert result.secids == ["SBER"]


async def test_invalid_intervals_are_rejected_before_any_request(env):
    for poll, report in (("1m", "1h"), ("1h", "15m"), ("18:00", "1h"), ("15m", "8d")):
        with pytest.raises(InvalidArguments):
            await env.service.set(1, ["SBER"], poll, report)

    assert env.exchange.requests == []
    assert (await env.service.status(1)).active is False


async def test_stop_removes_the_watch_and_repeated_stop_is_not_an_error(env):
    await env.service.set(1, ["SBER"], "15m", "1h")

    assert (await env.service.stop(1)).stopped is True
    assert (await env.service.status(1)).active is False
    assert (await env.service.stop(1)).stopped is False


async def test_stop_without_a_watch_reports_that_there_was_none(env):
    result = await env.service.stop(99)

    assert (result.chat_id, result.stopped) == (99, False)


async def test_stopped_chat_is_no_longer_polled(env):
    await env.service.set(1, ["SBER"], "15m", "1h")
    await env.service.stop(1)
    env.clock.advance(HOUR)
    before = len(env.exchange.requests)

    result = await env.service.run_due()

    assert len(env.exchange.requests) == before
    assert result.polled.chats == 0 and result.reports == [] and result.next_due_at is None


async def test_status_of_a_chat_without_a_watch_is_inactive(env):
    status = await env.service.status(5)

    assert status.active is False
    assert status.model_dump() == {"active": False}


async def test_status_shows_settings_deadlines_and_counters_in_moscow_time(env):
    await env.service.set(1, ["SBER", "TMOS"], "15m", "1h")

    status = await env.service.status(1)

    assert status.model_dump() == {
        "active": True,
        "chat_id": 1,
        "secids": ["SBER", "TMOS"],
        "poll_interval": "15m",
        "report_interval": "1h",
        "started_at": iso(),
        "next_poll_at": iso(POLL),
        "next_report_at": iso(HOUR),
        "samples_in_period": 2,
        "pending_reports": 0,
    }
    assert status.started_at.endswith("+03:00")


# --- 4.2 run_due ----------------------------------------------------------------------------------


async def test_nothing_due_means_no_exchange_requests_and_next_due_is_the_nearest_deadline(env):
    await env.service.set(1, ["SBER", "TMOS"], "15m", "1h")
    env.clock.advance(POLL - 1)
    before = len(env.exchange.requests)

    result = await env.service.run_due()

    assert len(env.exchange.requests) == before
    assert result.reports == []
    assert (result.polled.chats, result.polled.tickers, result.polled.failed) == (0, 0, 0)
    assert result.next_due_at == iso(POLL)
    assert result.now == iso(POLL - 1)


async def test_poll_due_stores_a_sample_per_ticker_and_moves_the_deadline_from_the_call(env):
    await env.service.set(1, ["SBER", "TMOS"], "15m", "1h")
    env.clock.advance(POLL + 100)  # вызов чуть позже срока

    result = await env.service.run_due()

    assert (result.polled.chats, result.polled.tickers, result.polled.failed) == (1, 2, 0)
    assert (await env.service.status(1)).samples_in_period == 4
    assert result.next_due_at == iso(POLL + 100 + POLL)  # от момента вызова, а не от прежнего срока
    assert result.reports == []


async def test_report_due_returns_a_report_with_aggregates_and_text(env):
    env.exchange.prices["SBER"] = 100
    await env.service.set(1, ["SBER"], "15m", "45m")
    for offset, price in ((POLL, 105), (2 * POLL, 95)):
        env.exchange.prices["SBER"] = price
        env.clock.now = T0 + offset
        assert (await env.service.run_due()).reports == []
    env.exchange.prices["SBER"] = 102
    env.clock.now = T0 + 3 * POLL

    result = await env.service.run_due()

    (report,) = result.reports
    (ticker,) = report.tickers
    assert isinstance(report.report_id, int) and report.chat_id == 1
    assert (report.period_start, report.period_end) == (iso(), iso(3 * POLL))
    assert (ticker.samples, ticker.first_price, ticker.last_price) == (4, 100, 102)
    assert (ticker.change_percent, ticker.min_price, ticker.max_price) == (2.0, 95, 105)
    assert "SBER: 4 замера, 100,0 -> 102,0 (+2,0%), мин 95,0, макс 105,0" in report.text
    assert "не инвестиционная рекомендация" in report.text
    assert result.next_due_at == iso(4 * POLL)  # следующий опрос раньше следующей сводки (от момента вызова)


async def test_missed_periods_give_one_sample_and_one_report(env):
    await env.service.set(1, ["SBER"], "15m", "1h")
    before = env.exchange.quote_requests("SBER")
    env.clock.advance(5 * HOUR)

    result = await env.service.run_due()

    assert env.exchange.quote_requests("SBER") == before + 1
    (report,) = result.reports
    assert (report.period_start, report.period_end) == (iso(), iso(5 * HOUR))
    assert report.tickers[0].samples == 2  # первый замер при постановке и один после простоя
    assert result.next_due_at == iso(5 * HOUR + POLL)
    again = await env.service.run_due()
    assert [r.report_id for r in again.reports] == [report.report_id]  # новой сводки нет


async def test_next_report_period_starts_at_the_previous_report(env):
    await env.service.set(1, ["SBER"], "15m", "15m")
    env.clock.advance(POLL)
    (first,) = (await env.service.run_due()).reports
    env.clock.advance(POLL)

    second = (await env.service.run_due()).reports[-1]

    assert second.report_id != first.report_id
    assert second.period_start == first.period_end == iso(POLL)
    assert second.tickers[0].samples == 1  # замер на границе входит только в одну сводку


async def test_failure_of_one_ticker_does_not_break_the_call_and_is_counted_in_the_report(env):
    await env.service.set(1, ["SBER", "TMOS"], "15m", "15m")
    env.exchange.down = {"TMOS"}
    env.clock.advance(POLL)

    result = await env.service.run_due()

    assert (result.polled.tickers, result.polled.failed) == (2, 1)
    (report,) = result.reports
    sber, tmos = report.tickers
    assert (sber.samples, sber.failed_samples) == (2, 0)
    assert (tmos.samples, tmos.failed_samples) == (1, 1)
    assert "неудавшихся замеров: 1" in report.text


async def test_ticker_with_only_failures_is_reported_as_no_data(env):
    await env.service.set(1, ["SBER"], "15m", "15m")
    await env.service.set(1, ["TMOS"], "15m", "15m")
    env.exchange.down = {"TMOS"}
    env.clock.advance(POLL)

    (report,) = (await env.service.run_due()).reports

    assert report.tickers[0].samples == 1  # первый замер при постановке удался
    env.clock.advance(POLL)
    (_, second) = (await env.service.run_due()).reports
    assert second.tickers[0].samples == 0 and second.tickers[0].failed_samples == 1
    assert "TMOS: данных нет (неудавшихся замеров: 1)" in second.text


async def test_exchange_outage_is_not_an_error_and_deadlines_move(env):
    await env.service.set(1, ["SBER", "TMOS"], "15m", "1h")
    env.exchange.all_down = True
    env.clock.advance(POLL)

    down = await env.service.run_due()

    assert (down.polled.chats, down.polled.tickers, down.polled.failed) == (1, 2, 2)
    assert down.next_due_at == iso(2 * POLL)  # сдвиг от момента вызова: следующий вызов повторит опрос вовремя
    env.exchange.all_down = False
    env.clock.now = T0 + 2 * POLL

    up = await env.service.run_due()

    assert up.polled.failed == 0
    status = await env.service.status(1)
    assert status.samples_in_period == 4  # 2 первых и 2 после восстановления; неудавшиеся не в счёт


async def test_one_ticker_in_two_chats_is_polled_once_per_call(env):
    await env.service.set(1, ["SBER"], "15m", "1h")
    await env.service.set(2, ["SBER", "TMOS"], "15m", "1h")
    env.clock.advance(POLL)
    before = env.exchange.quote_requests("SBER")

    result = await env.service.run_due()

    assert env.exchange.quote_requests("SBER") == before + 1
    assert (result.polled.chats, result.polled.tickers) == (2, 2)
    assert (await env.service.status(1)).samples_in_period == 2
    assert (await env.service.status(2)).samples_in_period == 4


async def test_no_watches_gives_no_reports_and_null_next_due(env):
    result = await env.service.run_due()

    assert result.reports == [] and result.has_more is False
    assert result.next_due_at is None
    assert result.polled.model_dump() == {"chats": 0, "tickers": 0, "failed": 0}


async def test_watch_replaced_during_polling_discards_the_old_samples(env):
    await env.service.set(1, ["SBER"], "15m", "1h")
    env.clock.advance(POLL)

    async def replace_meanwhile():
        await env.service.set(1, ["TMOS"], "5m", "30m")

    env.exchange.on_quote = replace_meanwhile

    result = await env.service.run_due()

    assert result.polled.tickers == 1
    status = await env.service.status(1)
    assert status.secids == ["TMOS"]
    assert status.samples_in_period == 1  # только первый замер нового опроса: замер SBER выброшен


async def test_old_samples_are_purged_by_run_due(env):
    await env.service.set(1, ["SBER"], "1d", "7d")
    env.clock.advance(31 * 24 * HOUR)

    (report,) = (await env.service.run_due()).reports

    assert report.tickers[0].samples == 1  # замер при постановке (старше 30 суток) в сводку не попал, остался свежий
    conn = sqlite3.connect(env.db_path)
    try:
        assert [row[0] for row in conn.execute("SELECT observed_at FROM observations")] == [T0 + 31 * 24 * HOUR]
    finally:
        conn.close()


# --- 4.3 get_report и ack -------------------------------------------------------------------------


async def test_get_report_covers_the_current_period_without_side_effects(env):
    env.exchange.prices["SBER"] = 100
    await env.service.set(1, ["SBER"], "15m", "1h")
    env.exchange.prices["SBER"] = 110
    env.clock.advance(POLL)
    await env.service.run_due()
    env.clock.advance(60)
    status_before = await env.service.status(1)
    requests_before = len(env.exchange.requests)

    report = await env.service.get_report(1)

    assert report.report_id is None and report.chat_id == 1
    assert (report.period_start, report.period_end) == (iso(), iso(POLL + 60))
    assert (report.tickers[0].samples, report.tickers[0].change_percent) == (2, 10.0)
    assert len(env.exchange.requests) == requests_before  # замеров не делает
    assert await env.service.status(1) == status_before  # сроки и счётчики те же
    assert (await env.service.run_due()).reports == []  # сводка не сохранялась


async def test_get_report_for_a_chat_without_a_watch_says_so(env):
    with pytest.raises(WatchNotSet, match="опрос не задан"):
        await env.service.get_report(7)


async def test_get_report_right_after_set_has_the_first_samples(env):
    await env.service.set(1, ["SBER", "TMOS"], "15m", "1h")

    report = await env.service.get_report(1)

    assert [t.samples for t in report.tickers] == [1, 1]


async def test_unacknowledged_report_is_delivered_again_until_acknowledged(env):
    await env.service.set(1, ["SBER"], "15m", "15m")
    env.clock.advance(POLL)
    (report,) = (await env.service.run_due()).reports
    assert (await env.service.status(1)).pending_reports == 1

    again = await env.service.run_due()

    assert [r.report_id for r in again.reports] == [report.report_id]
    assert again.reports[0].text == report.text
    assert (await env.service.ack([report.report_id])).acknowledged == 1
    assert (await env.service.run_due()).reports == []
    assert (await env.service.status(1)).pending_reports == 0


async def test_repeated_and_unknown_acknowledgement_are_not_errors(env):
    await env.service.set(1, ["SBER"], "15m", "15m")
    env.clock.advance(POLL)
    (report,) = (await env.service.run_due()).reports

    assert (await env.service.ack([report.report_id])).acknowledged == 1
    assert (await env.service.ack([report.report_id])).acknowledged == 0
    assert (await env.service.ack([999_999])).acknowledged == 0
    assert (await env.service.ack([])).acknowledged == 0


async def test_new_reports_come_after_old_unacknowledged_ones_oldest_first(env):
    await env.service.set(1, ["SBER"], "15m", "15m")
    env.clock.advance(POLL)
    await env.service.run_due()
    env.clock.advance(POLL)

    reports = (await env.service.run_due()).reports

    assert len(reports) == 2
    assert reports[0].report_id < reports[1].report_id


async def test_more_than_a_hundred_reports_come_in_batches_with_has_more(env):
    await env.service.set(1, ["SBER"], "5m", "5m")
    result = None
    for _ in range(105):
        env.clock.advance(300)
        result = await env.service.run_due()

    assert len(result.reports) == 100 and result.has_more is True
    ids = [r.report_id for r in result.reports]
    assert ids == sorted(ids)
    await env.service.ack(ids)
    rest = await env.service.run_due()
    assert len(rest.reports) == 5 and rest.has_more is False


async def test_unacknowledged_reports_survive_a_replaced_watch(env):
    await env.service.set(1, ["SBER"], "15m", "15m")
    env.clock.advance(POLL)
    (report,) = (await env.service.run_due()).reports

    await env.service.set(1, ["TMOS"], "15m", "1h")

    assert [r.report_id for r in (await env.service.run_due()).reports] == [report.report_id]
    assert (await env.service.status(1)).pending_reports == 1


async def test_stop_drops_unacknowledged_reports(env):
    await env.service.set(1, ["SBER"], "15m", "15m")
    env.clock.advance(POLL)
    await env.service.run_due()

    await env.service.stop(1)

    assert (await env.service.run_due()).reports == []


# --- 4.4 сквозной сценарий, перезапуск, изоляция ----------------------------------------------------


async def test_end_to_end_with_restart_keeps_data_and_deadlines(env):
    env.exchange.prices["SBER"] = 100
    await env.service.set(1, ["SBER", "TMOS"], "15m", "1h")
    for offset, price in ((POLL, 101), (2 * POLL, 102), (3 * POLL, 103)):
        env.exchange.prices["SBER"] = price
        env.clock.now = T0 + offset
        assert (await env.service.run_due()).reports == []
    env.exchange.prices["SBER"] = 104
    env.clock.now = T0 + 4 * POLL

    (report,) = (await env.service.run_due()).reports
    assert (report.tickers[0].samples, report.tickers[0].first_price, report.tickers[0].last_price) == (5, 100, 104)
    assert (await env.service.ack([report.report_id])).acknowledged == 1
    env.exchange.prices["SBER"] = 105
    env.clock.now = T0 + 5 * POLL
    await env.service.run_due()
    status_before = await env.service.status(1)

    service = env.restart()  # процесс завершился и запущен заново на том же файле

    assert await service.status(1) == status_before
    assert status_before.samples_in_period == 2  # по замеру каждого из двух тикеров после сводки
    env.exchange.prices["SBER"] = 106
    env.clock.now = T0 + 6 * POLL
    await service.run_due()
    report_after = await service.get_report(1)
    assert report_after.tickers[0].samples == 2  # замер до перезапуска и после
    assert (report_after.tickers[0].first_price, report_after.tickers[0].last_price) == (105, 106)
    assert (await service.run_due()).reports == []  # подтверждённая сводка не вернулась


async def test_two_chats_are_isolated(env):
    await env.service.set(1, ["SBER"], "15m", "1h")
    await env.service.set(2, ["TMOS", "IMOEX"], "5m", "30m")

    status_2 = await env.service.status(2)
    report_2 = await env.service.get_report(2)
    await env.service.stop(1)

    assert (await env.service.status(1)).active is False
    assert await env.service.status(2) == status_2
    assert [t.secid for t in report_2.tickers] == ["TMOS", "IMOEX"]
    assert [t.secid for t in (await env.service.get_report(2)).tickers] == ["TMOS", "IMOEX"]
    with pytest.raises(WatchNotSet):
        await env.service.get_report(1)


async def test_stopping_one_chat_keeps_the_other_polled(env):
    await env.service.set(1, ["SBER"], "15m", "1h")
    await env.service.set(2, ["TMOS"], "15m", "1h")
    await env.service.stop(1)
    env.clock.advance(POLL)

    result = await env.service.run_due()

    assert (result.polled.chats, result.polled.tickers) == (1, 1)
    assert (await env.service.status(2)).samples_in_period == 2
