import pytest

from mcp_moex.analytics.series import validate_history
from mcp_moex.errors import (
    HistoryInconsistent,
    HistoryTruncated,
    IntervalNotSupported,
    NoHistoryData,
)

from .analytics_helpers import make_history


def test_a_history_as_returned_by_get_price_history_is_accepted():
    history = make_history("SBER", [100, 120, 90, 110, 130])

    series = validate_history(history, "history")

    assert series.secid == "SBER"
    assert [bar.close for bar in series.bars] == [100, 120, 90, 110, 130]
    assert series.bars[0].begin.utcoffset().total_seconds() == 3 * 3600


@pytest.mark.parametrize("interval", ["day", "week", "month"])
def test_supported_intervals_are_accepted(interval):
    assert validate_history(make_history(closes=[100, 101, 102], interval=interval), "history").interval == interval


def test_truncated_history_is_rejected_with_advice():
    history = make_history(closes=[100, 101, 102], truncated=True)

    with pytest.raises(HistoryTruncated) as error:
        validate_history(history, "history")

    assert '"week"' in str(error.value) and "короткий период" in str(error.value)


def test_hourly_history_is_rejected_and_supported_intervals_are_listed():
    history = make_history(closes=[100, 101, 102]).model_copy(update={"interval": "hour"})

    with pytest.raises(IntervalNotSupported) as error:
        validate_history(history, "history")

    assert "day, week, month" in str(error.value)


def test_history_without_candles_says_there_is_no_data():
    history = make_history(closes=[100, 101]).model_copy(
        update={"candles": [], "summary": None, "candles_returned": 0, "message": "нет данных"}
    )

    with pytest.raises(NoHistoryData) as error:
        validate_history(history, "history")

    assert "нет данных" in str(error.value)


def test_candles_without_summary_are_inconsistent():
    history = make_history(closes=[100, 101]).model_copy(update={"summary": None})

    with pytest.raises(HistoryInconsistent):
        validate_history(history, "history")


@pytest.mark.parametrize(
    "field, value",
    [
        ("candles_count", 5),
        ("start_price", 99.0),
        ("end_price", 111.0),
        ("min_price", 90.0),
        ("max_price", 200.0),
    ],
)
def test_summary_that_disagrees_with_candles_means_changed_or_damaged_history(field, value):
    history = make_history(closes=[100, 110, 105])
    history = history.model_copy(update={"summary": history.summary.model_copy(update={field: value})})

    with pytest.raises(HistoryInconsistent) as error:
        validate_history(history, "history")

    text = str(error.value)
    assert "изменена или повреждена" in text and "заново" in text and field in text


def test_summary_is_compared_with_a_tolerance_not_bit_for_bit():
    history = make_history(closes=[100, 110, 105])
    summary = history.summary.model_copy(update={"end_price": 105.0 * (1 + 1e-12)})

    assert validate_history(history.model_copy(update={"summary": summary}), "history")


def test_unordered_candles_are_rejected():
    history = make_history(closes=[100, 110, 105])
    history = history.model_copy(update={"candles": list(reversed(history.candles))})

    with pytest.raises(HistoryInconsistent) as error:
        validate_history(history, "history")

    assert "не по возрастанию" in str(error.value)


def test_duplicate_candles_are_rejected():
    history = make_history(closes=[100, 110, 105])
    candles = [history.candles[0], history.candles[0], history.candles[2]]

    with pytest.raises(HistoryInconsistent) as error:
        validate_history(history.model_copy(update={"candles": candles}), "history")

    assert "повторяется" in str(error.value)


@pytest.mark.parametrize("field", ["open", "close"])
@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_non_positive_or_non_finite_prices_are_rejected(field, bad):
    history = make_history(closes=[100, 110, 105])
    candles = list(history.candles)
    candles[1] = candles[1].model_copy(update={field: bad})

    with pytest.raises(HistoryInconsistent) as error:
        validate_history(history.model_copy(update={"candles": candles}), "history")

    assert "повреждена" in str(error.value)


def test_the_error_names_the_rejected_parameter():
    history = make_history(closes=[100, 101, 102], truncated=True)

    with pytest.raises(HistoryTruncated) as error:
        validate_history(history, "history_b")

    assert "history_b" in str(error.value)
