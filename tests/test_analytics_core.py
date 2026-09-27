import math
from datetime import date

import pytest

from mcp_moex.analytics import core
from mcp_moex.analytics.series import validate_history
from mcp_moex.errors import TooFewCommonCandles

from .analytics_helpers import make_begins, make_history

TODAY = date(2026, 9, 27)


def series(*args, param="history", **kwargs):
    return validate_history(make_history(*args, **kwargs), param)


# --- выравнивание --------------------------------------------------------------------------------


def test_alignment_keeps_only_common_dates_and_measures_from_the_first_common_open():
    long = series("SBER", [100, 102, 104, 106, 108], opens=[99, 100, 102, 104, 106])
    short = series("IMOEX", [3000, 3030, 3060], asset_type="index", start=date(2026, 1, 7))

    aligned = core.align(long, short)

    assert aligned.begins == short_begins(short)
    assert aligned.common == 3
    # первая общая свеча SBER (7 января) идёт от её open (102-й), а не от close предыдущей свечи, которой в окне нет
    assert aligned.a.ratios[0] == pytest.approx(104 / 102)
    assert aligned.a.change_pct == pytest.approx(round((108 - 102) / 102 * 100, 2))


def short_begins(s):
    return [bar.iso for bar in s.bars]


def test_a_gap_in_one_series_stretches_the_change_over_the_missing_candle():
    days = make_begins(4)
    gappy = series("GAZP", [100, 110, 121], begins=[days[0], days[1], days[3]])
    full = series("SBER", [10, 11, 12, 13])

    aligned = core.align(gappy, full)

    assert aligned.common == 3
    assert aligned.a.period_pct[-1] == 10.0  # 121 / 110 - 1 по общим свечам


def test_fewer_than_two_common_candles_is_an_error():
    a = series("SBER", [100, 101], start=date(2026, 1, 5))
    b = series("GAZP", [100, 101], start=date(2026, 1, 6))

    with pytest.raises(TooFewCommonCandles) as error:
        core.align(a, b)

    assert "слишком короткий" in str(error.value)


def test_periods_multiply_to_the_total_change_with_rounding_tolerance():
    closes = [100 + (7 * i) % 13 - 6 + i * 0.3 for i in range(60)]
    leg = core.make_leg(series("SBER", closes))

    assert math.prod(leg.ratios) == pytest.approx(closes[-1] / leg.bars[0].open, rel=1e-12)
    rounded = math.prod(1 + pct / 100 for pct in leg.period_pct)
    assert abs(rounded - closes[-1] / leg.bars[0].open) * 100 <= 0.005 * len(closes) * rounded


# --- незавершённая свеча -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "interval, begin, expected",
    [
        ("month", date(2026, 9, 1), True),  # сентябрь ещё идёт
        ("month", date(2026, 8, 1), False),
        ("week", date(2026, 9, 21), True),  # понедельник 21.09 .. воскресенье 27.09: сегодня последний день недели
        ("week", date(2026, 9, 14), False),
        ("day", date(2026, 9, 27), True),
        ("day", date(2026, 9, 26), False),
    ],
)
def test_partial_flag_by_interval(interval, begin, expected):
    leg = core.make_leg(series("SBER", [100], interval=interval, start=begin))

    assert core.partial_flags(leg, TODAY)[-1] is expected


def test_last_day_of_a_month_handles_december_and_february():
    from datetime import datetime

    assert core.period_last_day(datetime(2026, 12, 1), "month") == date(2026, 12, 31)
    assert core.period_last_day(datetime(2028, 2, 1), "month") == date(2028, 2, 29)


def test_year_by_months_marks_only_the_running_month_as_partial():
    leg = core.make_leg(series("SBER", list(range(100, 112)), interval="month", start=date(2025, 10, 1)))

    flags = core.partial_flags(leg, date(2026, 9, 15))

    assert len(flags) == 12
    assert flags.count(True) == 1 and flags[-1] is True


# --- замечания -----------------------------------------------------------------------------------


def test_a_close_that_halves_is_reported_as_a_possible_split_but_is_not_an_error():
    leg = core.make_leg(series("GAZP", [100, 101, 50, 51]))

    assert core.split_suspects(leg) == [leg.begins[2]]
    note = core.split_note(leg)
    assert "сплит" in note and "GAZP" in note and "искажены" in note


@pytest.mark.parametrize("closes, flagged", [([100, 180], True), ([100, 55], True), ([100, 56], False), ([100, 179], False)])
def test_split_threshold_is_1_8_in_either_direction(closes, flagged):
    assert bool(core.split_suspects(core.make_leg(series("X", closes)))) is flagged


def test_a_consolidation_is_reported_too():
    assert core.split_suspects(core.make_leg(series("X", [10, 10.5, 105])))


def test_metrics_notes_always_carry_the_dividends_disclaimer():
    leg = core.make_leg(series("SBER", [100, 101, 102, 103], interval="month", start=date(2025, 1, 1)))

    notes = core.metrics_notes(leg, 1.0, TODAY)

    assert notes[0] == core.DIVIDENDS_NOTE


def test_bond_note_and_small_sample_note_for_two_candles():
    leg = core.make_leg(series("SU26238RMFS4", [95, 96], asset_type="bond", start=date(2025, 1, 1), interval="month"))

    notes = core.metrics_notes(leg, None, TODAY)

    assert any("процентах от номинала" in n and "накопленного купонного дохода" in n for n in notes)
    assert any("Выборка мала" in n and "волатильность не рассчитана" in n for n in notes)


def test_partial_last_candle_is_reported():
    leg = core.make_leg(series("SBER", [100, 101, 102], interval="month", start=date(2026, 7, 1)))

    assert any("не завершён" in n for n in core.metrics_notes(leg, 5.0, TODAY))


def test_a_shorter_common_window_is_reported_with_the_actual_window():
    full = series("IMOEX", list(range(3000, 3012)), asset_type="index", interval="month", start=date(2025, 1, 1))
    late = series("NEWCO", [50, 51, 52, 53, 54], interval="month", start=date(2025, 8, 1))

    aligned = core.align(late, full)
    notes = core.comparison_notes(aligned, 0.5, date(2026, 12, 31), benchmark=True)

    window = next(n for n in notes if n.startswith("Окно расчёта"))
    assert "01.08.2025" in window and "01.12.2025" in window and "общих свечей: 5" in window and "IMOEX" in window
    assert "NEWCO" not in window  # у NEWCO окно совпадает со всей историей


def test_no_window_note_when_the_histories_coincide():
    a, b = series("SBER", [100, 101, 102, 103]), series("GAZP", [50, 51, 52, 53])

    notes = core.comparison_notes(core.align(a, b), 0.5, TODAY, benchmark=False)

    assert not any(n.startswith("Окно расчёта") for n in notes)


def test_different_quote_currencies_produce_a_warning_but_not_an_error():
    a = series("SBER", [100, 101, 102, 103])
    b = series("YNDX", [10, 11, 12, 13], price_unit="USD")

    notes = core.comparison_notes(core.align(a, b), 0.5, TODAY, benchmark=False)

    assert any("RUB и USD" in n and "разных валютах" in n for n in notes)


def test_same_currency_gives_no_currency_warning_and_benchmark_gets_its_own_note():
    a, b = series("SBER", [100, 101, 102, 103]), series("IMOEX", [3000, 3001, 3002, 3003], asset_type="index")

    notes = core.comparison_notes(core.align(a, b), 0.5, TODAY, benchmark=True)

    assert not any("валют" in n for n in notes)
    assert core.BENCHMARK_NOTE in notes


def test_correlation_notes_explain_why_it_is_null():
    three = core.align(series("A", [100, 101, 99]), series("B", [50, 51, 50]))
    flat = core.align(series("A", [100, 100, 100, 100]), series("B", [50, 51, 50, 52]))

    assert any("общих свечей меньше четырёх" in n for n in core.comparison_notes(three, None, TODAY, benchmark=False))
    assert any("нулевой" in n for n in core.comparison_notes(flat, None, TODAY, benchmark=False))


def test_candles_skipped_inside_the_window_are_reported_but_are_not_a_narrowed_window():
    gappy = series("GAZP", [100, 110, 121], begins=[d for i, d in enumerate(make_begins(4)) if i != 2])
    full = series("SBER", [10, 11, 12, 13])

    notes = core.comparison_notes(core.align(gappy, full), 0.5, TODAY, benchmark=False)

    assert not any(n.startswith("Окно расчёта") for n in notes)
    assert any("Пропущено свечей: SBER (1)" in n for n in notes)
