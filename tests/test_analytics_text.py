import re
from datetime import date

import pytest

from mcp_moex.analytics import build, text
from mcp_moex.analytics.series import validate_history

from .analytics_helpers import make_history

TODAY = date(2026, 9, 27)
DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")
NUMBER_RE = re.compile(r"[+-]?\d+,\d+")
SPLIT_RATIO_TEXT = "1,8"


def s(param, *args, **kwargs):
    return validate_history(make_history(*args, **kwargs), param)


@pytest.fixture
def metrics():
    closes = [100, 120, 90, 110, 130, 125, 140, 138, 150, 149, 160, 158]
    return build.metrics(s("history", "SBER", closes, interval="month", start=date(2025, 10, 1)), TODAY)


@pytest.fixture
def benchmark():
    stock = s("history", "SBER", [100, 120, 90, 110, 130, 125], interval="month", start=date(2026, 4, 1))
    index = s("benchmark_history", "IMOEX", [3000, 3100, 2900, 3050, 3200, 3150], interval="month", start=date(2026, 4, 1), asset_type="index")
    return build.benchmark(stock, index, TODAY)


@pytest.fixture
def pair():
    a = s("history_a", "SBER", [100, 120, 90, 110, 130, 125], interval="month", start=date(2026, 4, 1))
    b = s("history_b", "GAZP", [50, 55, 27, 30, 33, 35], interval="month", start=date(2026, 4, 1))  # сплит вдвое в июне
    return build.pair(a, b, TODAY)


def numbers_in(result_text: str) -> set[str]:
    return set(NUMBER_RE.findall(DATE_RE.sub("", result_text)))


def result_numbers(result) -> set[str]:
    values: list[float] = []
    for name, value in result:
        if isinstance(value, float):
            values.append(value)
        elif hasattr(value, "depth_pct"):
            values.append(value.depth_pct)
    formatted = set()
    for value in values:
        formatted |= {text.format_plain(value), text.format_change(value)}
    return formatted


def whole_numbers_of_the_structure_appear(result_text: str, result, fields: list[str]) -> None:
    for field in fields:
        value = getattr(result, field)
        assert value is not None, field
        assert text.format_plain(value) in result_text or text.format_change(value) in result_text, field


@pytest.mark.parametrize("fixture_name", ["metrics", "benchmark", "pair"])
def test_every_decimal_number_in_the_text_comes_from_the_structure(fixture_name, request):
    result = request.getfixturevalue(fixture_name)

    strays = numbers_in(result.text) - result_numbers(result) - {SPLIT_RATIO_TEXT}

    assert not strays


def test_metrics_text_states_the_structured_numbers(metrics):
    whole_numbers_of_the_structure_appear(
        metrics.text, metrics, ["change_pct", "volatility_annualized_pct", "current_drawdown_pct"]
    )
    assert text.format_plain(metrics.max_drawdown.depth_pct) in metrics.text
    assert f"свечей: {metrics.candles_used}" in metrics.text
    assert "01.10.2025" in metrics.text and "01.09.2026" in metrics.text


def test_benchmark_text_states_the_structured_numbers(benchmark):
    whole_numbers_of_the_structure_appear(
        benchmark.text, benchmark, ["change_pct", "benchmark_change_pct", "difference_pp", "beta", "correlation"]
    )
    assert "SBER" in benchmark.text and "IMOEX" in benchmark.text


def test_pair_text_names_the_roles_and_the_difference_rule(pair):
    assert "A это SBER" in pair.text and "B это GAZP" in pair.text
    assert "изменение A минус изменение B" in pair.text
    whole_numbers_of_the_structure_appear(pair.text, pair, ["a_change_pct", "b_change_pct", "difference_pp", "correlation"])
    assert "бета" not in pair.text.lower()


@pytest.mark.parametrize("fixture_name", ["metrics", "benchmark", "pair"])
def test_text_has_period_and_both_closing_disclaimers_and_calls_the_value_a_price_change(fixture_name, request):
    result = request.getfixturevalue(fixture_name)

    assert DATE_RE.search(result.text)
    assert "Изменение цены" in result.text or "изменения цены" in result.text
    assert text.PAST_DISCLAIMER in result.text
    assert text.ADVICE_DISCLAIMER in result.text
    assert result.text.endswith(text.FOOTER)


@pytest.mark.parametrize("fixture_name", ["metrics", "benchmark", "pair"])
def test_text_has_no_guarantees_and_no_buy_or_sell_advice(fixture_name, request):
    result = request.getfixturevalue(fixture_name)
    body = result.text.removesuffix(text.FOOTER).lower()

    for word in ("купи", "продай", "продав", "покупа", "гаранти", "обязательно вырастет", "рекомендуем"):
        assert word not in body, word


@pytest.mark.parametrize("fixture_name", ["metrics", "benchmark", "pair"])
def test_notes_are_repeated_in_the_text_so_that_it_can_be_sent_as_is(fixture_name, request):
    result = request.getfixturevalue(fixture_name)

    assert result.notes
    for note in result.notes:
        assert f"- {note}" in result.text


def test_number_formatting_uses_a_decimal_comma_and_a_sign_for_changes():
    assert text.format_change(3.25) == "+3,25"
    assert text.format_change(-1.2) == "-1,2"
    assert text.format_change(0.0) == "0,0"
    assert text.format_plain(-25.0) == "-25,0"
    assert text.format_plain(1.0) == "1,0"
    assert text.format_date("2026-09-01T00:00:00+03:00") == "01.09.2026"


def test_no_drawdown_and_unrecovered_drawdown_are_worded_differently():
    flat = build.metrics(s("history", "SBER", [100, 101, 102, 103]), TODAY)
    unrecovered = build.metrics(s("history", "SBER", [100, 120, 90, 110]), TODAY)

    assert "просадки не было" in flat.text
    assert "не восстановлена" in unrecovered.text
