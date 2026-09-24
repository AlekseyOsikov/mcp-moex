from datetime import datetime

import pytest

from mcp_moex.errors import MarketDataUnavailable, SecurityNotFound, UnsupportedAssetClass
from mcp_moex.iss import IssClient
from mcp_moex.tools.price import get_current_price

from .fake_iss import RECORD_ENV, CountingTransport, FixtureTransport, MutatingTransport, set_column

BOND_FIELDS = {"face_value", "currency", "price_rub", "accrued_interest", "dirty_price", "yield_percent"}
SBER_PATH = "engines/stock/markets/shares/boards/TQBR/securities/SBER.json"
QUOTE_PARAMS = {"iss.only": "marketdata,securities"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv(RECORD_ENV, raising=False)


@pytest.fixture
def iss() -> IssClient:
    return IssClient(transport=FixtureTransport())


async def raw(iss: IssClient, path: str) -> tuple[dict, dict]:
    """Строки marketdata и securities из записанного ответа: ожидаемые значения берём оттуда."""
    tables = await iss.get_tables(path, QUOTE_PARAMS)
    return tables["marketdata"][0], tables["securities"][0]


# --- 5.1 акции и фонды ------------------------------------------------------------------------


async def test_share_price_is_last_trade_in_rubles(iss):
    marketdata, _ = await raw(iss, SBER_PATH)

    result = await get_current_price(iss, "SBER")

    assert result.secid == "SBER"
    assert result.name == "Сбербанк"
    assert result.asset_type == "stock"
    assert result.price == marketdata["LAST"] > 0
    assert result.price_unit == "RUB"
    assert result.price_source == "last_trade"
    assert result.trading_status == marketdata["TRADINGSTATUS"]
    assert not BOND_FIELDS & result.model_dump().keys()


async def test_share_as_of_is_moscow_time_of_the_quote(iss):
    marketdata, _ = await raw(iss, SBER_PATH)

    result = await get_current_price(iss, "SBER")

    as_of = datetime.fromisoformat(result.as_of)
    assert result.as_of.endswith("+03:00")
    assert as_of.strftime("%H:%M:%S") == marketdata["TIME"]
    assert as_of.strftime("%Y-%m-%d") == marketdata["SYSTIME"][:10]


async def test_share_is_marked_as_delayed_by_15_minutes(iss):
    result = await get_current_price(iss, "SBER")

    assert result.delayed is True
    assert result.delay_minutes == 15


async def test_lowercase_ticker_returns_canonical_secid(iss):
    result = await get_current_price(iss, "sber")

    assert result.secid == "SBER"


async def test_fund_price_is_in_rubles(iss):
    marketdata, _ = await raw(iss, "engines/stock/markets/shares/boards/TQBR/securities/TMOS.json")

    result = await get_current_price(iss, "TMOS")

    assert result.asset_type == "fund"
    assert result.price_unit == "RUB"
    assert result.price == marketdata["LAST"] > 0
    assert (result.delayed, result.delay_minutes) == (True, 15)


async def test_without_trades_today_previous_close_is_used():
    def no_trades(path, payload):
        if path.endswith("SBER.json") and "marketdata" in payload:
            set_column(payload, "marketdata", "LAST", None)

    client = IssClient(transport=MutatingTransport(no_trades))
    _, security = await raw(IssClient(transport=FixtureTransport()), SBER_PATH)

    result = await get_current_price(client, "SBER")

    assert result.price_source == "previous_close"
    assert result.price == security["PREVPRICE"]
    assert result.as_of == f"{security['PREVDATE']}T23:59:59+03:00"


async def test_quote_time_later_than_snapshot_belongs_to_previous_day():
    def after_midnight(path, payload):
        if path.endswith("SBER.json") and "marketdata" in payload:
            set_column(payload, "marketdata", "SYSTIME", "2026-09-25 00:05:00")
            set_column(payload, "marketdata", "TIME", "23:50:00")

    result = await get_current_price(IssClient(transport=MutatingTransport(after_midnight)), "SBER")

    assert result.as_of == "2026-09-24T23:50:00+03:00"


async def test_currency_code_sur_is_reported_as_rub_and_other_codes_are_kept():
    def dollar(path, payload):
        if path.endswith("SBER.json") and "securities" in payload:
            set_column(payload, "securities", "CURRENCYID", "USD")

    result = await get_current_price(IssClient(transport=MutatingTransport(dollar)), "SBER")

    assert result.price_unit == "USD"


# --- 5.2 облигации ----------------------------------------------------------------------------


async def test_bond_price_is_percent_of_face_with_rub_price_and_accrued_interest(iss):
    marketdata, security = await raw(iss, "engines/stock/markets/bonds/boards/TQOB/securities/SU26238RMFS4.json")

    result = await get_current_price(iss, "SU26238RMFS4")

    assert result.asset_type == "bond"
    assert result.price_unit == "percent_of_face"
    assert result.price == marketdata["LAST"]
    assert result.face_value == security["FACEVALUE"] == 1000
    assert result.currency == "RUB"
    assert result.price_rub == pytest.approx(result.price / 100 * result.face_value)
    assert result.accrued_interest == security["ACCRUEDINT"]
    assert result.dirty_price == pytest.approx(result.price_rub + result.accrued_interest)
    assert result.yield_percent == marketdata["YIELD"]
    assert (result.delayed, result.delay_minutes) == (True, 15)


async def test_bond_rub_price_has_no_float_noise():
    def fixed(path, payload):
        if "SU26238RMFS4.json" in path and "marketdata" in payload:
            set_column(payload, "marketdata", "LAST", 51.79)
        if "SU26238RMFS4.json" in path and "securities" in payload:
            set_column(payload, "securities", "ACCRUEDINT", 22.17)

    result = await get_current_price(IssClient(transport=MutatingTransport(fixed)), "SU26238RMFS4")

    assert result.price_rub == 517.9
    assert result.dirty_price == 540.07


async def test_bond_without_yield_keeps_the_field_as_null():
    def no_yield(path, payload):
        if "SU26238RMFS4.json" in path and "marketdata" in payload:
            set_column(payload, "marketdata", "YIELD", None)

    result = await get_current_price(IssClient(transport=MutatingTransport(no_yield)), "SU26238RMFS4")

    dumped = result.model_dump()
    assert "yield_percent" in dumped
    assert dumped["yield_percent"] is None


# --- 5.3 индексы ------------------------------------------------------------------------------


async def test_index_value_is_in_points_without_delay_and_bond_fields(iss):
    marketdata, _ = await raw(iss, "engines/stock/markets/index/boards/SNDX/securities/IMOEX.json")

    result = await get_current_price(iss, "IMOEX")

    assert result.asset_type == "index"
    assert result.price == marketdata["CURRENTVALUE"] > 0
    assert result.price_unit == "points"
    assert (result.delayed, result.delay_minutes) == (False, 0)
    assert not BOND_FIELDS & result.model_dump().keys()


# --- ошибки и кэш -----------------------------------------------------------------------------


async def test_unknown_ticker_is_an_error_with_search_hint(iss):
    with pytest.raises(SecurityNotFound, match="search_securities"):
        await get_current_price(iss, "NOPE123")


async def test_futures_contract_is_an_unsupported_class(iss):
    with pytest.raises(UnsupportedAssetClass):
        await get_current_price(iss, "SiZ6")


async def test_security_without_market_data_is_an_error_not_an_empty_result():
    def empty(path, payload):
        if path.endswith("SBER.json") and "marketdata" in payload:
            payload["marketdata"]["data"] = []

    with pytest.raises(MarketDataUnavailable, match="SBER"):
        await get_current_price(IssClient(transport=MutatingTransport(empty)), "SBER")


async def test_repeated_price_requests_within_ttl_hit_iss_once():
    transport = CountingTransport()
    client = IssClient(transport=transport)

    await get_current_price(client, "SBER")
    await get_current_price(client, "SBER")

    assert transport.count("boards/TQBR/securities/SBER.json") == 1
    assert transport.count("securities/SBER.json") == 2  # карточка бумаги и котировка: по одному разу


async def test_index_on_rtsi_board_works_like_index_on_sndx(iss):
    marketdata, _ = await raw(iss, "engines/stock/markets/index/boards/RTSI/securities/IMOEXCNY.json")

    result = await get_current_price(iss, "IMOEXCNY")

    assert result.asset_type == "index"
    assert result.price == marketdata["CURRENTVALUE"] > 0
    assert result.price_unit == "points"
