import pytest

from mcp_moex.errors import (
    BenchmarkMustBeIndex,
    BondNotComparable,
    HistoryInconsistent,
    HistoryTruncated,
    IndexNotAllowed,
    IntervalMismatch,
    IntervalNotSupported,
    MoexError,
    NoHistoryData,
    TooFewCommonCandles,
)

CASES = [
    (HistoryTruncated("history"), ["history", "interval", '"week"', "короткий период"]),
    (IntervalNotSupported("history_a", "hour"), ["history_a", "hour", "day, week, month"]),
    (NoHistoryData("benchmark_history"), ["benchmark_history", "нет данных", "get_price_history"]),
    (HistoryInconsistent("history_b", "end_price не равен close"), ["history_b", "заново", "без изменений"]),
    (IntervalMismatch("history_a", "day", "history_b", "month"), ["history_a", "history_b", "одним и тем же", "interval"]),
    (BenchmarkMustBeIndex("benchmark_history", "SBER", "stock"), ["benchmark_history", "индекс", "compare_securities"]),
    (IndexNotAllowed("history_a", "IMOEX"), ["history_a", "IMOEX", "compare_with_benchmark"]),
    (BondNotComparable("history", "SU26238RMFS4"), ["history", "процентах от номинала", "купонного дохода", "не поддерживается"]),
    (TooFewCommonCandles(1), ["общих свечей 1", "слишком короткий"]),
]


@pytest.mark.parametrize("error, fragments", CASES, ids=lambda value: type(value).__name__ if isinstance(value, MoexError) else "")
def test_error_text_names_the_parameter_and_tells_what_to_do(error, fragments):
    assert isinstance(error, MoexError)
    for fragment in fragments:
        assert fragment in str(error), fragment


def test_truncated_history_advises_a_coarser_interval_or_a_shorter_period():
    text = str(HistoryTruncated("history"))

    assert '"week"' in text and '"month"' in text
    assert "короткий период" in text


def test_every_error_is_a_domain_error_so_the_guard_shows_its_text():
    for error, _ in CASES:
        assert isinstance(error, MoexError)
