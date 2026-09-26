"""Агрегаты и текст сводки: чистые функции."""

from datetime import datetime

import pytest

from mcp_moex.tools.price import MSK
from mcp_moex.watch.report import (
    DISCLAIMER,
    aggregate,
    build_report,
    format_change,
    format_number,
    plural_samples,
)
from mcp_moex.watch.store import Sample


def ts(day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(2026, 9, day, hour, minute, tzinfo=MSK).timestamp())


START, END = ts(26, 12), ts(26, 13)


def ok(secid: str, price: float, at: int, unit: str = "RUB", quote_at: int | None = None) -> Sample:
    return Sample(
        secid=secid, observed_at=at, ok=True, price=price, price_unit=unit, price_source="last_trade",
        quote_at=at if quote_at is None else quote_at,
    )  # fmt: skip


def failed(secid: str, at: int) -> Sample:
    return Sample(secid=secid, observed_at=at, ok=False)


# --- 3.1 агрегаты -------------------------------------------------------------------------------


def test_aggregate_of_four_samples():
    samples = [ok("SBER", p, ts(26, 12, m)) for p, m in ((100, 0), (105, 15), (95, 30), (102, 45))]

    result = aggregate("SBER", samples)

    assert (result.samples, result.failed_samples) == (4, 0)
    assert (result.first_price, result.last_price) == (100, 102)
    assert result.change_percent == 2.0
    assert (result.min_price, result.max_price) == (95, 105)
    assert result.price_unit == "RUB"
    assert result.last_quote_at == "2026-09-26T12:45:00+03:00"


def test_aggregate_orders_samples_by_time_not_by_input_order():
    samples = [ok("SBER", 102, ts(26, 12, 45)), ok("SBER", 100, ts(26, 12, 0))]

    result = aggregate("SBER", samples)

    assert (result.first_price, result.last_price) == (100, 102)


def test_aggregate_of_a_single_sample():
    result = aggregate("SBER", [ok("SBER", 301.2, START)])

    assert result.samples == 1
    assert result.first_price == result.last_price == 301.2
    assert result.change_percent == 0.0
    assert result.min_price == result.max_price == 301.2


def test_aggregate_of_only_failures_has_no_prices():
    result = aggregate("GAZP", [failed("GAZP", START), failed("GAZP", ts(26, 12, 15))])

    assert (result.samples, result.failed_samples) == (0, 2)
    assert result.first_price is result.last_price is result.change_percent is None
    assert result.min_price is result.max_price is result.price_unit is result.last_quote_at is None


def test_aggregate_counts_failures_next_to_good_samples():
    result = aggregate("SBER", [ok("SBER", 100, START), failed("SBER", ts(26, 12, 15)), ok("SBER", 101, ts(26, 12, 30))])

    assert (result.samples, result.failed_samples) == (2, 1)


def test_aggregate_without_any_samples():
    result = aggregate("SBER", [])

    assert (result.samples, result.failed_samples, result.last_price) == (0, 0, None)


def test_aggregate_ignores_other_tickers():
    result = aggregate("SBER", [ok("GAZP", 999, START), ok("SBER", 100, START)])

    assert result.samples == 1 and result.max_price == 100


def test_change_percent_is_rounded_to_two_decimals_and_can_be_negative():
    result = aggregate("SBER", [ok("SBER", 300, START), ok("SBER", 299, ts(26, 12, 15))])

    assert result.change_percent == -0.33


def test_last_quote_time_comes_from_the_last_sample():
    samples = [ok("SBER", 100, START, quote_at=ts(26, 11, 40)), ok("SBER", 101, ts(26, 12, 15), quote_at=ts(26, 12, 0))]

    assert aggregate("SBER", samples).last_quote_at == "2026-09-26T12:00:00+03:00"


def test_report_lists_every_ticker_of_the_watch_even_without_samples():
    report = build_report(5, ["SBER", "GAZP"], [ok("SBER", 100, START)], START, END)

    assert [t.secid for t in report.tickers] == ["SBER", "GAZP"]
    assert report.tickers[1].samples == 0
    assert report.chat_id == 5 and report.report_id is None
    assert (report.period_start, report.period_end) == ("2026-09-26T12:00:00+03:00", "2026-09-26T13:00:00+03:00")


def test_report_without_any_samples_is_still_built():
    report = build_report(5, ["SBER"], [], START, END)

    assert report.tickers[0].samples == 0
    assert "SBER: данных нет (замеров не было)" in report.text


# --- 3.2 текст ------------------------------------------------------------------------------------


def test_exact_text_for_shares_failures_and_disclaimer():
    samples = [
        ok("SBER", 301.2, ts(26, 12, 0), quote_at=ts(26, 11, 45)),
        ok("SBER", 300.9, ts(26, 12, 15)),
        ok("SBER", 303.4, ts(26, 12, 30)),
        ok("SBER", 303.0, ts(26, 12, 45), quote_at=ts(26, 12, 45)),
        *[failed("GAZP", ts(26, 12, m)) for m in (0, 15, 30, 45)],
    ]

    text = build_report(1, ["SBER", "GAZP"], samples, START, END).text

    assert text == (
        "Сводка за 26.09 12:00 - 13:00 (МСК)\n"
        "SBER: 4 замера, 301,2 -> 303,0 (+0,6%), мин 300,9, макс 303,4, котировка на 12:45\n"
        "GAZP: данных нет (неудавшихся замеров: 4)\n"
        "Котировки акций, облигаций и фондов задержаны на 15 минут, индексы без задержки. "
        "Это не инвестиционная рекомендация."
    )


def test_single_sample_line():
    text = build_report(1, ["SBER"], [ok("SBER", 301.2, START, quote_at=ts(26, 11, 45))], START, END).text

    assert "SBER: 1 замер, 301,2, котировка на 11:45" in text


def test_partial_failures_are_mentioned_next_to_the_prices():
    samples = [ok("SBER", 100, START), failed("SBER", ts(26, 12, 15)), ok("SBER", 101, ts(26, 12, 30))]

    text = build_report(1, ["SBER"], samples, START, END).text

    assert "2 замера, 100,0 -> 101,0 (+1,0%)" in text
    assert "неудавшихся замеров: 1" in text


def test_bond_prices_are_marked_as_percent_of_face():
    samples = [ok("SU26238RMFS4", 57.7, START, unit="percent_of_face"), ok("SU26238RMFS4", 58.1, ts(26, 12, 30), unit="percent_of_face")]

    text = build_report(1, ["SU26238RMFS4"], samples, START, END).text

    assert "57,7 -> 58,1 % от номинала (+0,69%)" in text


def test_share_prices_carry_no_percent_of_face_mark():
    text = build_report(1, ["SBER"], [ok("SBER", 100, START), ok("SBER", 101, ts(26, 12, 30))], START, END).text

    assert "от номинала" not in text


@pytest.mark.parametrize("samples", [[], [failed("SBER", START)], [ok("SBER", 1, START)]])
def test_both_disclaimers_are_in_any_report(samples):
    text = build_report(1, ["SBER"], samples, START, END).text

    assert "задержаны на 15 минут" in text
    assert "не инвестиционная рекомендация" in text
    assert text.endswith(DISCLAIMER)


def test_period_across_days_names_both_dates_and_old_quote_names_its_date():
    start, end = ts(25, 18, 0), ts(26, 10, 0)

    text = build_report(1, ["SBER"], [ok("SBER", 100, start, quote_at=ts(25, 18, 39))], start, end).text

    assert text.splitlines()[0] == "Сводка за 25.09 18:00 - 26.09 10:00 (МСК)"
    assert "котировка на 25.09 18:39" in text


@pytest.mark.parametrize(
    ("count", "phrase"),
    [(1, "1 замер"), (2, "2 замера"), (4, "4 замера"), (5, "5 замеров"), (11, "11 замеров"), (12, "12 замеров"),
     (21, "21 замер"), (22, "22 замера"), (100, "100 замеров")],
)  # fmt: skip
def test_sample_count_plural(count, phrase):
    assert plural_samples(count) == phrase


@pytest.mark.parametrize(
    ("value", "text"), [(303.0, "303,0"), (301.2, "301,2"), (0.6, "0,6"), (12345.67, "12345,67")]
)
def test_number_format_uses_decimal_comma(value, text):
    assert format_number(value) == text


@pytest.mark.parametrize(
    ("value", "text"), [(0.6, "+0,6"), (2.0, "+2,0"), (-1.25, "-1,25"), (0.0, "0,0"), (0.69, "+0,69"), (-0.33, "-0,33")]
)
def test_change_format(value, text):
    assert format_change(value) == text
