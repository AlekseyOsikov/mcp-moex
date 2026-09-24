"""Фикстуры ISS должны отдаваться транспортом без обращения к сети."""

import httpx
import pytest

from mcp_moex.iss import IssClient
from mcp_moex.tools.search import SEARCH_COLUMNS

from .fake_iss import RECORD_ENV, FixtureTransport, MissingFixture
from .record_fixtures import recordings


@pytest.fixture
def iss(monkeypatch) -> IssClient:
    monkeypatch.delenv(RECORD_ENV, raising=False)
    return IssClient(transport=FixtureTransport())


def test_every_recorded_request_has_a_fixture_file():
    from .fake_iss import fixture_path

    for path, params in recordings():
        url = httpx.URL(f"https://iss.moex.com/iss/{path}", params={"iss.meta": "off", **params})
        assert fixture_path(url).exists(), f"нет фикстуры для {path} {params}"


async def test_security_card_fixtures_have_expected_shape(iss):
    sber = await iss.get_tables("securities/SBER.json", {"iss.only": "description,boards"})
    description = {row["name"]: row["value"] for row in sber["description"]}
    primary = [row for row in sber["boards"] if row["is_primary"] == 1]

    assert description["SECID"] == "SBER"
    assert description["GROUP"] == "stock_shares"
    assert [row["boardid"] for row in primary] == ["TQBR"]

    unknown = await iss.get_tables("securities/NOPE123.json", {"iss.only": "description,boards"})
    assert unknown["description"] == []
    assert unknown["boards"] == []


async def test_quote_and_candle_fixtures_are_served_offline(iss):
    quote = await iss.get_tables(
        "engines/stock/markets/bonds/boards/TQOB/securities/SU26238RMFS4.json",
        {"iss.only": "marketdata,securities"},
    )
    assert quote["marketdata"][0]["SECID"] == "SU26238RMFS4"
    assert quote["securities"][0]["FACEVALUE"] == 1000

    candles = await iss.get_tables(
        "engines/stock/markets/index/boards/SNDX/securities/IMOEX/candles.json",
        {"from": "2026-09-01", "till": "2026-09-10", "interval": 24, "start": 0},
    )
    assert candles["candles"], "у индекса должны быть свечи за период"
    assert {"open", "close", "high", "low", "value", "volume", "begin", "end"} <= candles["candles"][0].keys()


async def test_search_fixture_is_served_offline(iss):
    found = await iss.get_tables(
        "securities.json",
        {
            "q": "TGLD",
            "is_trading": 1,
            "group_by": "group",
            "group_by_filter": "stock_index",
            "limit": 100,
            "iss.only": "securities",
            "securities.columns": SEARCH_COLUMNS,
        },
    )
    assert {row["secid"] for row in found["securities"]} >= {"TGLDA", "TGLDB"}


async def test_unrecorded_request_fails_loudly_instead_of_using_the_network(iss):
    with pytest.raises(MissingFixture, match="RECORD_FIXTURES"):
        await iss.get_tables("securities/NEVER_RECORDED.json")
