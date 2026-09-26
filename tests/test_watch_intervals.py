import pytest

from mcp_moex.errors import InvalidArguments, WatchNotSet, WatchStoreUnavailable
from mcp_moex.watch.intervals import (
    format_seconds,
    parse_interval,
    parse_poll_interval,
    parse_report_interval,
)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("5m", 300), ("15m", 900), ("1h", 3600), ("24h", 86400), ("1d", 86400)],
)
def test_valid_poll_intervals(text, seconds):
    assert parse_poll_interval(text).seconds == seconds


def test_interval_is_normalized_to_lowercase_without_spaces_or_leading_zeros():
    interval = parse_poll_interval("  015M ")

    assert (interval.text, interval.seconds) == ("15m", 900)


def test_five_minutes_poll_and_one_day_report_are_accepted():
    poll = parse_poll_interval("5m")

    assert parse_report_interval("1d", poll).seconds == 86400


def test_poll_interval_bounds_are_inclusive_five_minutes_to_one_day():
    assert parse_poll_interval("5m").seconds == 300
    assert parse_poll_interval("1d").seconds == 86400
    for text in ("4m", "1m", "25h", "2d"):
        with pytest.raises(InvalidArguments) as error:
            parse_poll_interval(text)
        assert "poll_interval" in str(error.value)
        assert "5m" in str(error.value) and "1d" in str(error.value)


def test_one_minute_poll_names_the_minimum():
    with pytest.raises(InvalidArguments, match=r"poll_interval.*5m"):
        parse_poll_interval("1m")


@pytest.mark.parametrize("text", ["18:00", "каждый час", "", "15", "m", "1.5h", "-5m", "0m", "1w", "1 h", "15мин"])
def test_invalid_format_gives_examples(text):
    with pytest.raises(InvalidArguments) as error:
        parse_poll_interval(text)

    message = str(error.value)
    assert "poll_interval" in message
    assert "15m" in message and "1h" in message and "1d" in message


def test_report_interval_cannot_be_shorter_than_poll_interval():
    poll = parse_poll_interval("1h")

    with pytest.raises(InvalidArguments) as error:
        parse_report_interval("15m", poll)

    message = str(error.value)
    assert "report_interval" in message
    assert "не может быть меньше периода опроса" in message


def test_report_interval_may_equal_poll_and_is_capped_at_seven_days():
    poll = parse_poll_interval("1h")

    assert parse_report_interval("1h", poll).seconds == 3600
    assert parse_report_interval("7d", poll).seconds == 7 * 86400
    with pytest.raises(InvalidArguments, match=r"report_interval.*7d"):
        parse_report_interval("8d", poll)


def test_report_interval_format_error_names_the_report_parameter():
    with pytest.raises(InvalidArguments, match="report_interval"):
        parse_report_interval("18:00", parse_poll_interval("1h"))


def test_parse_interval_names_the_given_parameter():
    with pytest.raises(InvalidArguments, match="whatever"):
        parse_interval("x", "whatever")


def test_format_seconds_uses_the_largest_exact_unit():
    assert [format_seconds(s) for s in (300, 5400, 3600, 7200, 86400, 7 * 86400)] == [
        "5m",
        "90m",
        "1h",
        "2h",
        "1d",
        "7d",
    ]


def test_watch_errors_are_russian_and_suggest_a_next_step():
    not_set = str(WatchNotSet(42))
    unavailable = str(WatchStoreUnavailable("disk I/O error"))

    assert "42" in not_set and "watch_set" in not_set
    assert "--watch-db" in unavailable and "disk I/O error" in unavailable
    for text in (not_set, unavailable):
        assert "Traceback" not in text
