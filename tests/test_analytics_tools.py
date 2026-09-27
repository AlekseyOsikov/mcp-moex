from datetime import date

import pytest

from mcp_moex.errors import (
    BenchmarkMustBeIndex,
    BondNotComparable,
    HistoryTruncated,
    IndexNotAllowed,
    IntervalMismatch,
    TooFewCommonCandles,
)
from mcp_moex.iss import IssClient
from mcp_moex.models import HistoryResult
from mcp_moex.tools import analytics
from mcp_moex.tools.history import get_price_history

from .analytics_helpers import make_history
from .fake_iss import RECORD_ENV, CountingTransport

TODAY = date(2026, 9, 27)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv(RECORD_ENV, raising=False)


@pytest.fixture
def transport() -> CountingTransport:
    return CountingTransport()


@pytest.fixture
async def histories(transport):
    """Реальные фикстуры свечей (дневные, 2026-09-01…2026-09-10), как их отдаёт `get_price_history`."""
    iss = IssClient(transport=transport)
    return {
        secid: await get_price_history(iss, secid, "2026-09-01", "2026-09-10", "day")
        for secid in ("SBER", "IMOEX", "SU26238RMFS4")
    }


def as_client_sends_it(history: HistoryResult) -> HistoryResult:
    """История, прошедшая через JSON, как её передаёт клиент."""
    return HistoryResult.model_validate_json(history.model_dump_json())


# --- compute_price_metrics -----------------------------------------------------------------------


async def test_metrics_accept_the_history_as_is_and_agree_with_its_summary(histories):
    history = as_client_sends_it(histories["SBER"])

    result = await analytics.compute_price_metrics(history, TODAY)

    assert result.secid == "SBER"
    assert result.change_pct == history.summary.change_pct
    assert result.candles_used == history.summary.candles_count
    assert result.first_candle == history.summary.first_candle
    assert result.last_candle == history.summary.last_candle
    assert result.max_drawdown.depth_pct <= 0
    assert result.notes[0].startswith("Изменение цены не учитывает дивиденды")


async def test_metrics_for_a_bond_pass_with_the_face_value_warning(histories):
    result = await analytics.compute_price_metrics(histories["SU26238RMFS4"], TODAY)

    assert result.asset_type == "bond"
    assert any("процентах от номинала" in n and "накопленного купонного дохода" in n for n in result.notes)


async def test_metrics_for_an_index_pass_too(histories):
    result = await analytics.compute_price_metrics(histories["IMOEX"], TODAY)

    assert result.price_unit == "points"


async def test_periods_are_one_per_candle_and_the_last_is_partial_while_the_day_runs(histories):
    result = await analytics.compute_price_metrics(histories["SBER"], date(2026, 9, 10))

    assert len(result.periods) == histories["SBER"].summary.candles_count
    assert result.periods[-1].partial is True
    assert not any(period.partial for period in result.periods[:-1])
    assert any("не завершён" in n for n in result.notes)


async def test_truncated_history_is_rejected():
    with pytest.raises(HistoryTruncated):
        await analytics.compute_price_metrics(make_history(closes=[1, 2, 3], truncated=True), TODAY)


# --- compare_with_benchmark ----------------------------------------------------------------------


async def test_index_against_itself_is_a_perfect_match(histories):
    result = await analytics.compare_with_benchmark(histories["IMOEX"], histories["IMOEX"], TODAY)

    assert (result.difference_pp, result.beta, result.correlation) == (0, 1, 1)
    assert result.change_pct == result.benchmark_change_pct


async def test_stock_against_the_index_keeps_the_stock_change_of_its_own_summary(histories):
    result = await analytics.compare_with_benchmark(
        as_client_sends_it(histories["SBER"]), as_client_sends_it(histories["IMOEX"]), TODAY
    )

    # у SBER есть торги в выходные, у индекса нет: общих свечей меньше, но границы окна те же, что у периода акции
    assert result.candles_common == histories["IMOEX"].summary.candles_count
    assert result.first_candle == histories["SBER"].summary.first_candle
    assert result.last_candle == histories["SBER"].summary.last_candle
    assert result.change_pct == histories["SBER"].summary.change_pct
    assert not any(n.startswith("Окно расчёта") for n in result.notes)
    assert any("Пропущено свечей: SBER (2)" in n for n in result.notes)
    assert result.benchmark_change_pct == histories["IMOEX"].summary.change_pct
    assert result.difference_pp == round(result.change_pct - result.benchmark_change_pct, 2)
    assert len(result.periods) == result.candles_common
    assert -1 <= result.correlation <= 1


async def test_a_stock_as_the_benchmark_points_to_compare_securities(histories):
    with pytest.raises(BenchmarkMustBeIndex) as error:
        await analytics.compare_with_benchmark(histories["SBER"], histories["SBER"], TODAY)

    assert "compare_securities" in str(error.value) and "benchmark_history" in str(error.value)


async def test_a_bond_is_not_compared_with_an_index(histories):
    with pytest.raises(BondNotComparable) as error:
        await analytics.compare_with_benchmark(histories["SU26238RMFS4"], histories["IMOEX"], TODAY)

    assert "процентах от номинала" in str(error.value) and "history" in str(error.value)


async def test_different_intervals_are_rejected():
    daily = make_history("SBER", [100, 101, 102, 103])
    monthly = make_history("IMOEX", [3000, 3001, 3002, 3003], interval="month", asset_type="index")

    with pytest.raises(IntervalMismatch) as error:
        await analytics.compare_with_benchmark(daily, monthly, TODAY)

    assert "одним и тем же interval" in str(error.value)


async def test_a_short_common_period_is_rejected():
    stock = make_history("SBER", [100, 101, 102])
    index = make_history("IMOEX", [3000, 3001], asset_type="index", start=date(2026, 1, 7))

    with pytest.raises(TooFewCommonCandles):
        await analytics.compare_with_benchmark(stock, index, TODAY)


async def test_a_stock_that_started_trading_midway_narrows_the_window():
    index = make_history("IMOEX", list(range(3000, 3010)), asset_type="index")
    late = make_history("NEWCO", [50, 51, 52, 53, 54], start=date(2026, 1, 10))

    result = await analytics.compare_with_benchmark(late, index, TODAY)

    assert result.candles_common == 5
    assert result.first_candle.startswith("2026-01-10")
    assert any(n.startswith("Окно расчёта") for n in result.notes)


# --- compare_securities --------------------------------------------------------------------------


async def test_two_stocks_give_both_changes_the_difference_and_both_drawdowns():
    a = make_history("SBER", [100, 120, 90, 110, 130])
    b = make_history("GAZP", [50, 49, 52, 51, 55])

    result = await analytics.compare_securities(a, b, TODAY)

    assert (result.a_secid, result.b_secid) == ("SBER", "GAZP")
    assert result.a_change_pct == a.summary.change_pct and result.b_change_pct == b.summary.change_pct
    assert result.difference_pp == result.a_change_pct - result.b_change_pct
    assert result.a_max_drawdown.depth_pct == -25.0
    assert result.correlation is not None
    assert "beta" not in result.model_dump()
    assert all(p.difference_pp == round(p.a_change_pct - p.b_change_pct, 2) for p in result.periods)


async def test_a_security_against_itself(histories):
    result = await analytics.compare_securities(histories["SBER"], histories["SBER"], TODAY)

    assert result.difference_pp == 0 and result.correlation == 1


@pytest.mark.parametrize("index_first", [True, False])
async def test_an_index_points_to_compare_with_benchmark(histories, index_first):
    stock, index = histories["SBER"], histories["IMOEX"]
    args = (index, stock) if index_first else (stock, index)

    with pytest.raises(IndexNotAllowed) as error:
        await analytics.compare_securities(*args, TODAY)

    assert "compare_with_benchmark" in str(error.value)
    assert ("history_a" if index_first else "history_b") in str(error.value)


async def test_a_bond_is_not_compared_with_a_stock(histories):
    with pytest.raises(BondNotComparable):
        await analytics.compare_securities(histories["SBER"], histories["SU26238RMFS4"], TODAY)


async def test_different_quote_currencies_warn_but_do_not_fail():
    result = await analytics.compare_securities(
        make_history("SBER", [100, 101, 102, 103]),
        make_history("YNDX", [10, 11, 12, 13], price_unit="USD"),
        TODAY,
    )

    assert any("RUB и USD" in n for n in result.notes)


async def test_the_error_names_the_offending_parameter():
    good = make_history("SBER", [100, 101, 102])
    bad = make_history("GAZP", [100, 101, 102], truncated=True)

    with pytest.raises(HistoryTruncated) as error:
        await analytics.compare_securities(good, bad, TODAY)

    assert "history_b" in str(error.value)


# --- без сети ------------------------------------------------------------------------------------


async def test_analytics_never_touch_the_network(histories, transport):
    before = list(transport.paths)

    await analytics.compute_price_metrics(histories["SBER"], TODAY)
    await analytics.compare_with_benchmark(histories["SBER"], histories["IMOEX"], TODAY)
    await analytics.compare_securities(histories["SBER"], histories["SBER"], TODAY)

    assert transport.paths == before


# --- описания ------------------------------------------------------------------------------------

DESCRIPTIONS = {
    "metrics": analytics.METRICS_DESCRIPTION,
    "benchmark": analytics.BENCHMARK_DESCRIPTION,
    "securities": analytics.SECURITIES_DESCRIPTION,
}


@pytest.mark.parametrize("name", DESCRIPTIONS)
def test_every_description_states_the_common_rules(name):
    text = DESCRIPTIONS[name]

    assert "get_price_history" in text and "без изменений" in text
    assert "search_securities" in text
    assert '"week"' in text and '"month"' in text
    assert "изменение цены" in text and "не доходность" in text
    assert "дивиденды" in text and "купоны" in text and "сплиты" in text


@pytest.mark.parametrize("name", ["benchmark", "securities"])
def test_comparison_descriptions_say_no_bonds_and_one_interval(name):
    text = DESCRIPTIONS[name]

    assert "Облигации не сравниваются" in text
    assert "одним и тем же interval" in text


def test_descriptions_separate_the_two_comparisons():
    benchmark, securities = DESCRIPTIONS["benchmark"], DESCRIPTIONS["securities"]

    assert "сравнения с индексом" in benchmark and "compare_securities" in benchmark
    assert "не индексы" in securities and "compare_with_benchmark" in securities
