import httpx
import pytest

from mcp_moex.errors import SecurityNotFound, UnsupportedAssetClass
from mcp_moex.iss import IssClient
from mcp_moex.resolver import resolve

from .fake_iss import RECORD_ENV, FixtureTransport


@pytest.fixture
def iss(monkeypatch) -> IssClient:
    monkeypatch.delenv(RECORD_ENV, raising=False)
    return IssClient(transport=FixtureTransport())


def card(description: dict, boards: list[dict]) -> httpx.MockTransport:
    """Ответ карточки бумаги с заданными описанием и площадками."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "description": {
                    "columns": ["name", "value"],
                    "data": [[name, value] for name, value in description.items()],
                },
                "boards": {
                    "columns": ["boardid", "engine", "market", "is_primary"],
                    "data": [[b["boardid"], b["engine"], b["market"], b["is_primary"]] for b in boards],
                },
            },
        )

    return httpx.MockTransport(handler)


# --- 4.1 определение площадки и класса --------------------------------------------------------


async def test_share_resolves_to_primary_board(iss):
    instrument = await resolve(iss, "SBER")

    assert (instrument.engine, instrument.market, instrument.board) == ("stock", "shares", "TQBR")
    assert instrument.asset_type == "stock"
    assert instrument.secid == "SBER"
    assert instrument.name == "Сбербанк"
    assert instrument.isin == "RU0009029540"
    assert instrument.board_path == "engines/stock/markets/shares/boards/TQBR/securities/SBER"


async def test_bond_resolves_to_bond_board(iss):
    instrument = await resolve(iss, "SU26238RMFS4")

    assert (instrument.market, instrument.board) == ("bonds", "TQOB")
    assert instrument.asset_type == "bond"


async def test_fund_resolves_to_primary_board_not_the_empty_one(iss):
    instrument = await resolve(iss, "TMOS")

    assert instrument.board == "TQBR"  # у TMOS есть ещё пустая площадка TQTF, но она не основная
    assert instrument.asset_type == "fund"


async def test_index_resolves_to_index_market(iss):
    instrument = await resolve(iss, "IMOEX")

    assert (instrument.market, instrument.board) == ("index", "SNDX")
    assert instrument.asset_type == "index"
    assert instrument.isin is None


async def test_ticker_case_is_ignored_and_canonical_spelling_returned(iss):
    instrument = await resolve(iss, "sber")

    assert instrument.secid == "SBER"
    assert instrument.board == "TQBR"


@pytest.mark.parametrize(
    ("group", "expected"),
    [("stock_dr", "stock"), ("stock_etf", "fund"), ("stock_ppif", "fund"), ("stock_shares", "stock")],
)
async def test_groups_map_to_asset_types(group, expected):
    transport = card(
        {"SECID": "X1", "GROUP": group}, [{"boardid": "TQBR", "engine": "stock", "market": "shares", "is_primary": 1}]
    )

    instrument = await resolve(IssClient(transport=transport), "X1")

    assert instrument.asset_type == expected


async def test_primary_board_on_stock_engine_wins_over_other_engines():
    transport = card(
        {"SECID": "X1", "GROUP": "stock_shares"},
        [
            {"boardid": "MPTR", "engine": "otc", "market": "sharesndm", "is_primary": 1},
            {"boardid": "TQBR", "engine": "stock", "market": "shares", "is_primary": 1},
        ],
    )

    instrument = await resolve(IssClient(transport=transport), "X1")

    assert (instrument.engine, instrument.board) == ("stock", "TQBR")


# --- 4.2 неизвестные и неподдерживаемые -------------------------------------------------------


async def test_unknown_ticker_is_not_found(iss):
    with pytest.raises(SecurityNotFound) as error:
        await resolve(iss, "NOPE123")

    assert "search_securities" in str(error.value)


async def test_futures_contract_is_unsupported(iss):
    with pytest.raises(UnsupportedAssetClass) as error:
        await resolve(iss, "SiZ6")

    assert error.value.group == "futures_forts"
    assert "акции" in str(error.value)


async def test_calculated_indicator_is_not_treated_as_index():
    transport = card(
        {"SECID": "TGLDA", "GROUP": "stock_index"},
        [{"boardid": "INAV", "engine": "stock", "market": "index", "is_primary": 1}],
    )

    with pytest.raises(UnsupportedAssetClass, match="INAV"):
        await resolve(IssClient(transport=transport), "TGLDA")


async def test_supported_security_without_primary_board_is_not_found():
    transport = card(
        {"SECID": "OLD1", "GROUP": "stock_shares"},
        [{"boardid": "EQBR", "engine": "stock", "market": "shares", "is_primary": 0}],
    )

    with pytest.raises(SecurityNotFound):
        await resolve(IssClient(transport=transport), "OLD1")


# --- 4.3 кэш ----------------------------------------------------------------------------------


async def test_second_resolution_of_same_ticker_makes_no_network_request(monkeypatch):
    monkeypatch.delenv(RECORD_ENV, raising=False)
    fixtures = FixtureTransport()
    requests = 0

    class Counting(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return await fixtures.handle_async_request(request)

    client = IssClient(transport=Counting())

    first = await resolve(client, "SBER")
    second = await resolve(client, "SBER")

    assert requests == 1
    assert first == second
