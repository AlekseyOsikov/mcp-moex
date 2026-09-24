from datetime import datetime, timedelta

import httpx
import pytest

from mcp_moex.errors import InvalidArguments, RangeTooLarge, SecurityNotFound
from mcp_moex.iss import IssClient
from mcp_moex.tools.history import MAX_PAGES, MAX_RETURNED_CANDLES, MSK, PAGE_SIZE, get_price_history

from .fake_iss import RECORD_ENV, FixtureTransport

COLUMNS = ["open", "close", "high", "low", "value", "volume", "begin", "end"]
CANDLES_PARAMS = {"from": "2026-09-01", "till": "2026-09-10", "interval": 24, "start": 0}
SBER_CANDLES = "engines/stock/markets/shares/boards/TQBR/securities/SBER/candles.json"
OFZ_CANDLES = "engines/stock/markets/bonds/boards/TQOB/securities/SU26238RMFS4/candles.json"
IMOEX_CANDLES = "engines/stock/markets/index/boards/SNDX/securities/IMOEX/candles.json"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv(RECORD_ENV, raising=False)


@pytest.fixture
def iss() -> IssClient:
    return IssClient(transport=FixtureTransport())


class CandleTransport(httpx.AsyncBaseTransport):
    """Карточки бумаг из фикстур, а свечи синтетические: с настоящей постраничностью ISS (500 строк)."""

    def __init__(self, rows: list[list]) -> None:
        self._fixtures = FixtureTransport()
        self._rows = rows
        self.candle_requests: list[dict[str, str]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/candles.json"):
            return await self._fixtures.handle_async_request(request)
        params = dict(request.url.params)
        self.candle_requests.append(params)
        start = int(params["start"])
        page = self._rows[start : start + PAGE_SIZE]
        return httpx.Response(200, json={"candles": {"columns": COLUMNS, "data": page}})


def make_rows(count: int, *, first_open: float = 100.0) -> list[list]:
    """`count` часовых свечей: open растёт на 1, close = open + 0.5, high = close + 1, low = open - 1."""
    base = datetime(2026, 1, 5, 6, 0, 0)
    rows = []
    for i in range(count):
        begin = base + timedelta(hours=i)
        open_ = first_open + i
        rows.append(
            [
                open_,
                open_ + 0.5,
                open_ + 1.5,
                open_ - 1,
                1000.0,
                10,
                begin.strftime("%Y-%m-%d %H:%M:%S"),
                (begin + timedelta(minutes=59, seconds=59)).strftime("%Y-%m-%d %H:%M:%S"),
            ]
        )
    return rows


async def fixture_candles(iss: IssClient, path: str) -> list[dict]:
    return (await iss.get_tables(path, CANDLES_PARAMS))["candles"]


# --- 6.1 свечи и интервалы --------------------------------------------------------------------


async def test_daily_candles_of_a_share_follow_iss_data(iss):
    expected = await fixture_candles(iss, SBER_CANDLES)

    result = await get_price_history(iss, "SBER", "2026-09-01", "2026-09-10", "day")

    assert (result.secid, result.asset_type, result.interval) == ("SBER", "stock", "day")
    assert (result.date_from, result.date_till) == ("2026-09-01", "2026-09-10")
    assert result.price_unit == "RUB"
    assert len(result.candles) == len(expected) > 0
    first = result.candles[0]
    assert first.begin == "2026-09-01T00:00:00+03:00"
    assert (first.open, first.high, first.low, first.close) == (
        expected[0]["open"],
        expected[0]["high"],
        expected[0]["low"],
        expected[0]["close"],
    )
    assert first.volume == expected[0]["volume"]
    assert first.turnover == pytest.approx(expected[0]["value"])
    begins = [candle.begin for candle in result.candles]
    assert begins == sorted(begins)


async def test_index_candles_have_no_volume(iss):
    result = await get_price_history(iss, "IMOEX", "2026-09-01", "2026-09-10", "day")

    assert result.asset_type == "index"
    assert result.candles
    assert all(candle.volume is None for candle in result.candles)
    assert all(candle.turnover is not None for candle in result.candles)


@pytest.mark.parametrize(("interval", "code"), [("hour", "60"), ("day", "24"), ("week", "7"), ("month", "31")])
async def test_interval_names_map_to_iss_codes(interval, code):
    transport = CandleTransport(make_rows(3))

    result = await get_price_history(IssClient(transport=transport), "SBER", "2026-01-01", "2026-01-31", interval)

    assert transport.candle_requests[0]["interval"] == code
    assert result.interval == interval


async def test_dates_are_passed_to_iss_as_given():
    transport = CandleTransport(make_rows(1))

    await get_price_history(IssClient(transport=transport), "SBER", "2026-03-02", "2026-03-20")

    assert transport.candle_requests[0]["from"] == "2026-03-02"
    assert transport.candle_requests[0]["till"] == "2026-03-20"


# --- 6.2 постраничная выборка -----------------------------------------------------------------


async def test_all_pages_are_fetched_and_counted():
    rows = make_rows(1234)
    transport = CandleTransport(rows)

    result = await get_price_history(IssClient(transport=transport), "SBER", "2026-01-01", "2026-12-31", "hour")

    assert [int(r["start"]) for r in transport.candle_requests] == [0, 500, 1000]
    assert result.summary.candles_count == 1234


async def test_exactly_the_maximum_number_of_candles_is_allowed():
    transport = CandleTransport(make_rows(MAX_PAGES * PAGE_SIZE))

    result = await get_price_history(IssClient(transport=transport), "SBER", "2026-01-01", "2026-12-31", "hour")

    assert result.summary.candles_count == MAX_PAGES * PAGE_SIZE
    assert len(transport.candle_requests) == MAX_PAGES + 1  # последний запрос проверяет, что данных больше нет


async def test_more_than_the_maximum_is_a_range_error_with_advice():
    transport = CandleTransport(make_rows(MAX_PAGES * PAGE_SIZE + 1))

    with pytest.raises(RangeTooLarge) as error:
        await get_price_history(IssClient(transport=transport), "SBER", "2020-01-01", "2026-12-31", "hour")

    assert "day" in str(error.value)
    assert str(MAX_PAGES * PAGE_SIZE) in str(error.value)


# --- 6.3 сводка и ограничение размера ---------------------------------------------------------


async def test_summary_is_computed_over_all_candles_and_candles_are_truncated():
    rows = make_rows(MAX_RETURNED_CANDLES + 200)
    transport = CandleTransport(rows)

    result = await get_price_history(IssClient(transport=transport), "SBER", "2026-01-01", "2026-12-31", "hour")

    total = len(rows)
    assert result.summary.candles_count == total
    assert result.candles_truncated is True
    assert result.candles_returned == len(result.candles) == MAX_RETURNED_CANDLES
    assert result.candles[-1].begin.startswith(rows[-1][6].replace(" ", "T"))
    assert result.candles[0].begin.startswith(rows[total - MAX_RETURNED_CANDLES][6].replace(" ", "T"))
    assert result.summary.first_candle.startswith(rows[0][6].replace(" ", "T"))
    assert result.summary.last_candle == result.candles[-1].begin
    assert result.summary.start_price == rows[0][0]
    assert result.summary.end_price == rows[-1][1]
    assert result.summary.min_price == min(r[3] for r in rows)
    assert result.summary.max_price == max(r[2] for r in rows)


async def test_short_series_is_not_truncated():
    result = await get_price_history(
        IssClient(transport=CandleTransport(make_rows(5))), "SBER", "2026-01-01", "2026-01-31", "hour"
    )

    assert result.candles_truncated is False
    assert result.candles_returned == 5


@pytest.mark.parametrize(
    ("open_", "close", "change_abs", "change_pct"),
    [(200.0, 210.0, 10.0, 5.0), (3.0, 4.0, 1.0, 33.33), (50.0, 45.0, -5.0, -10.0), (0.1, 0.3, 0.2, 200.0)],
)
async def test_change_formula(open_, close, change_abs, change_pct):
    row = [open_, close, max(open_, close), min(open_, close), 1.0, 1, "2026-01-05 00:00:00", "2026-01-05 23:59:59"]

    result = await get_price_history(IssClient(transport=CandleTransport([row])), "SBER", "2026-01-05", "2026-01-05")

    assert result.summary.change_abs == change_abs
    assert result.summary.change_pct == change_pct
    assert result.summary.change_pct == round((close - open_) / open_ * 100, 2)


async def test_summary_of_real_data_is_consistent_with_candles(iss):
    result = await get_price_history(iss, "SBER", "2026-09-01", "2026-09-10", "day")

    summary = result.summary
    assert summary.candles_count == len(result.candles)
    assert summary.min_price <= min(c.low for c in result.candles)
    assert summary.max_price >= max(c.high for c in result.candles)
    assert summary.min_price == min(c.low for c in result.candles)
    assert summary.max_price == max(c.high for c in result.candles)
    assert summary.change_pct == round((summary.end_price - summary.start_price) / summary.start_price * 100, 2)


# --- 6.4 проверка периода ---------------------------------------------------------------------


async def test_inverted_period_is_rejected(iss):
    with pytest.raises(InvalidArguments, match="позже"):
        await get_price_history(iss, "SBER", "2026-09-10", "2026-09-01")


@pytest.mark.parametrize("bad", ["2026-13-45", "2026-02-30", "вчера", "2026/09/01"])
async def test_invalid_date_is_rejected_with_format_hint(iss, bad):
    with pytest.raises(InvalidArguments, match="YYYY-MM-DD"):
        await get_price_history(iss, "SBER", bad, "2026-09-10")
    with pytest.raises(InvalidArguments, match="date_till"):
        await get_price_history(iss, "SBER", "2026-09-01", bad)


async def test_period_end_defaults_to_today_in_moscow():
    transport = CandleTransport(make_rows(1))

    before = datetime.now(MSK).date().isoformat()
    result = await get_price_history(IssClient(transport=transport), "SBER", "2026-01-01")
    after = datetime.now(MSK).date().isoformat()

    assert result.date_till in {before, after}
    assert transport.candle_requests[0]["till"] == result.date_till


async def test_period_without_trading_is_a_normal_empty_result():
    result = await get_price_history(IssClient(transport=CandleTransport([])), "SBER", "2026-01-01", "2026-01-02")

    assert result.candles == []
    assert result.summary is None
    assert result.candles_returned == 0
    assert result.candles_truncated is False
    assert "нет данных" in result.message


async def test_unknown_ticker_is_an_error_with_search_hint(iss):
    with pytest.raises(SecurityNotFound, match="search_securities"):
        await get_price_history(iss, "NOPE123", "2026-09-01", "2026-09-10")


# --- 6.5 единицы ------------------------------------------------------------------------------


async def test_bond_history_is_in_percent_of_face_without_conversion(iss):
    expected = await fixture_candles(iss, OFZ_CANDLES)

    result = await get_price_history(iss, "SU26238RMFS4", "2026-09-01", "2026-09-10", "day")

    assert result.asset_type == "bond"
    assert result.price_unit == "percent_of_face"
    assert [c.close for c in result.candles] == [row["close"] for row in expected]
    assert all(0 < c.close < 200 for c in result.candles)  # проценты от номинала, а не рубли


async def test_index_history_is_in_points(iss):
    expected = await fixture_candles(iss, IMOEX_CANDLES)

    result = await get_price_history(iss, "IMOEX", "2026-09-01", "2026-09-10", "day")

    assert result.price_unit == "points"
    assert [c.close for c in result.candles] == [row["close"] for row in expected]


async def test_fund_history_is_in_the_currency_of_the_security():
    result = await get_price_history(
        IssClient(transport=CandleTransport(make_rows(2))), "TMOS", "2026-01-01", "2026-01-31", "hour"
    )

    assert result.asset_type == "fund"
    assert result.price_unit == "RUB"


# --- 6.6 предупреждение о нескорректированных ценах -------------------------------------------


def test_description_warns_about_unadjusted_prices_and_explains_units():
    from mcp_moex.tools.history import DESCRIPTION

    assert "не скорректированы" in DESCRIPTION
    assert "сплит" in DESCRIPTION
    assert "search_securities" in DESCRIPTION
    for unit in ("RUB", "percent_of_face", "points"):
        assert unit in DESCRIPTION
    assert str(MAX_RETURNED_CANDLES) in DESCRIPTION
