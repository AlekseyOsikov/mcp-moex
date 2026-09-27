import math

import pytest

from mcp_moex.analytics import stats

BEGINS = [f"2026-01-0{i}T00:00:00+03:00" for i in range(1, 8)]


# --- изменения ---------------------------------------------------------------------------------


def test_total_change_uses_the_same_expression_as_the_history_summary():
    assert stats.total_change_pct(100, 130) == 30.0
    assert stats.total_change_pct(287.4, 291.15) == round((291.15 - 287.4) / 287.4 * 100, 2)


def test_change_is_never_negative_zero():
    assert math.copysign(1, stats.total_change_pct(100, 100)) == 1
    assert math.copysign(1, stats.ratio_to_pct(1.0000001 - 1e-7)) == 1


def test_first_period_is_measured_from_open_and_the_rest_from_the_previous_close():
    ratios = stats.period_ratios(100, [110, 99, 108.9])

    assert [stats.ratio_to_pct(r) for r in ratios] == [10.0, -10.0, 10.0]


def test_period_ratios_multiply_to_last_close_over_first_open():
    closes = [101.5, 99.2, 104.8, 110.3, 108.1]

    assert math.prod(stats.period_ratios(98.0, closes)) == pytest.approx(closes[-1] / 98.0, rel=1e-12)


# --- волатильность -----------------------------------------------------------------------------


def test_volatility_is_null_below_three_candles():
    assert stats.volatility_annualized_pct([100, 110], "day") is None
    assert stats.volatility_annualized_pct([100], "month") is None


def test_volatility_is_sample_stdev_of_simple_returns_scaled_to_a_year():
    closes = [100, 110, 99]  # изменения +10 % и -10 %
    expected = math.sqrt(((0.1 - 0) ** 2 + (-0.1 - 0) ** 2) / 1) * math.sqrt(12) * 100

    assert stats.volatility_annualized_pct(closes, "month") == round(expected, 2)


@pytest.mark.parametrize("interval, factor", [("day", 252), ("week", 52), ("month", 12)])
def test_annualization_factor_per_interval(interval, factor):
    assert stats.annualization_factor(interval) == factor
    closes = [100, 102, 101, 104]
    daily = stats.volatility_annualized_pct(closes, interval)
    assert daily == round(
        math.sqrt(sum((r - sum(stats.close_returns(closes)) / 3) ** 2 for r in stats.close_returns(closes)) / 2)
        * math.sqrt(factor)
        * 100,
        2,
    )


def test_flat_series_has_zero_volatility():
    assert stats.volatility_annualized_pct([100, 100, 100, 100], "day") == 0.0


# --- просадка ----------------------------------------------------------------------------------


def test_max_drawdown_with_peak_trough_and_recovery():
    drawdown = stats.max_drawdown([100, 120, 90, 110, 130], BEGINS[:5])

    assert drawdown.depth_pct == -25.0
    assert drawdown.peak == BEGINS[1]
    assert drawdown.trough == BEGINS[2]
    assert drawdown.recovered_at == BEGINS[4]


def test_unrecovered_drawdown_has_no_recovery_date():
    drawdown = stats.max_drawdown([100, 120, 90, 110], BEGINS[:4])

    assert drawdown.depth_pct == -25.0
    assert drawdown.recovered_at is None


def test_recovery_needs_a_close_at_or_above_the_peak():
    assert stats.max_drawdown([100, 120, 90, 120], BEGINS[:4]).recovered_at == BEGINS[3]
    assert stats.max_drawdown([100, 120, 90, 119.99], BEGINS[:4]).recovered_at is None


def test_the_deepest_drawdown_wins_over_an_earlier_shallower_one():
    drawdown = stats.max_drawdown([100, 90, 100, 130, 65, 70], BEGINS[:6])

    assert drawdown.depth_pct == -50.0
    assert (drawdown.peak, drawdown.trough) == (BEGINS[3], BEGINS[4])


def test_equal_maxima_keep_the_earliest_peak():
    drawdown = stats.max_drawdown([100, 120, 120, 90], BEGINS[:4])

    assert drawdown.peak == BEGINS[1]


def test_non_decreasing_series_has_no_drawdown_at_all():
    drawdown = stats.max_drawdown([100, 100, 105, 110], BEGINS[:4])

    assert drawdown.model_dump() == {"depth_pct": 0.0, "peak": None, "trough": None, "recovered_at": None}
    assert stats.current_drawdown_pct([100, 100, 105, 110]) == 0.0


def test_current_drawdown_is_measured_from_the_highest_close():
    assert stats.current_drawdown_pct([100, 130, 117]) == -10.0


# --- корреляция и бета -------------------------------------------------------------------------


def test_series_against_itself_gives_exactly_one_and_one():
    returns = stats.close_returns([100, 103, 99, 104, 101, 108])

    assert stats.beta_and_correlation(returns, returns) == (1.0, 1.0)


def test_fewer_than_three_returns_give_null_for_both():
    two = stats.close_returns([100, 103, 99])

    assert len(two) == 2
    assert stats.beta_and_correlation(two, two) == (None, None)


def test_three_returns_are_enough():
    three = stats.close_returns([100, 103, 99, 104])

    assert stats.beta_and_correlation(three, three) == (1.0, 1.0)


@pytest.mark.parametrize("flat_first", [True, False])
def test_zero_spread_of_either_series_gives_null(flat_first):
    flat = [0.01, 0.01, 0.01, 0.01]
    moving = [0.01, -0.02, 0.03, 0.0]
    args = (flat, moving) if flat_first else (moving, flat)

    assert stats.beta_and_correlation(*args) == (None, None)


def test_beta_of_a_series_that_moves_twice_as_much_and_opposite_correlation():
    benchmark = [0.01, -0.02, 0.03, 0.0, -0.01]
    doubled = [2 * value for value in benchmark]
    inverted = [-value for value in benchmark]

    assert stats.beta_and_correlation(doubled, benchmark) == (2.0, 1.0)
    assert stats.beta_and_correlation(inverted, benchmark) == (-1.0, -1.0)
