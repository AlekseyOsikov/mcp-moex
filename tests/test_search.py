import httpx
import pytest

from mcp_moex.errors import InvalidArguments
from mcp_moex.iss import IssClient
from mcp_moex.tools.search import DESCRIPTION, ISS_SEARCH_LIMIT, SEARCH_COLUMNS, search_securities

from .fake_iss import RECORD_ENV, FixtureTransport


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv(RECORD_ENV, raising=False)


@pytest.fixture
def iss() -> IssClient:
    return IssClient(transport=FixtureTransport())


def secids(result) -> list[str]:
    return [hit.secid for hit in result.results]


def row(secid, group="stock_shares", board="TQBR", isin=None, name=None):
    return [secid, secid, name or f"Имя {secid}", isin, group, board]


def synthetic(by_group: dict[str, list[list]]) -> IssClient:
    """ISS, отвечающий на поиск заданными строками для каждой группы."""

    def handler(request: httpx.Request) -> httpx.Response:
        group = request.url.params["group_by_filter"]
        columns = SEARCH_COLUMNS.split(",")
        return httpx.Response(200, json={"securities": {"columns": columns, "data": by_group.get(group, [])}})

    return IssClient(transport=httpx.MockTransport(handler))


# --- 7.1 запрос и нормализация ----------------------------------------------------------------


async def test_search_by_name_in_stock_class_finds_both_share_types(iss):
    result = await search_securities(iss, "Сбербанк", "stock")

    assert {"SBER", "SBERP"} <= set(secids(result))
    assert {hit.asset_type for hit in result.results} == {"stock"}
    assert result.asset_type == "stock"
    assert result.query == "Сбербанк"


async def test_hits_are_normalized_and_have_all_fields(iss):
    result = await search_securities(iss, "Сбербанк", "stock")

    sber = next(hit for hit in result.results if hit.secid == "SBER")
    assert sber.model_dump() == {
        "secid": "SBER",
        "name": "Сбербанк России ПАО ао",
        "isin": "RU0009029540",
        "asset_type": "stock",
        "board": "TQBR",
    }
    assert result.count == len(result.results)


async def test_gold_search_in_fund_class_returns_funds_only(iss):
    result = await search_securities(iss, "золото", "fund")

    assert "TGLD" in secids(result)
    assert {hit.asset_type for hit in result.results} == {"fund"}


async def test_bond_search_finds_the_ofz(iss):
    result = await search_securities(iss, "ОФЗ 26238", "bond")

    assert secids(result) == ["SU26238RMFS4"]
    hit = result.results[0]
    assert (hit.asset_type, hit.board, hit.isin) == ("bond", "TQOB", "RU000A1038V6")


async def test_any_class_queries_every_group_and_labels_classes(iss):
    result = await search_securities(iss, "Сбербанк", "any", limit=20)

    classes = {hit.asset_type for hit in result.results}
    assert {"stock", "bond"} <= classes
    assert classes <= {"stock", "bond", "fund", "index"}
    # акции идут раньше облигаций: облигаций много и они не должны вытеснять остальное
    first_bond = next(i for i, hit in enumerate(result.results) if hit.asset_type == "bond")
    assert all(hit.asset_type != "stock" for hit in result.results[first_bond:])


async def test_only_traded_securities_are_requested(iss):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"securities": {"columns": SEARCH_COLUMNS.split(","), "data": []}})

    await search_securities(IssClient(transport=httpx.MockTransport(handler)), "x", "bond")

    assert seen[0]["is_trading"] == "1"
    assert seen[0]["group_by_filter"] == "stock_bonds"
    assert seen[0]["limit"] == str(ISS_SEARCH_LIMIT)


async def test_classes_with_two_iss_groups_are_searched_in_both():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["group_by_filter"])
        return httpx.Response(200, json={"securities": {"columns": SEARCH_COLUMNS.split(","), "data": []}})

    client = IssClient(transport=httpx.MockTransport(handler))
    await search_securities(client, "x", "fund")
    await search_securities(client, "y", "stock")

    assert sorted(seen) == ["stock_dr", "stock_etf", "stock_ppif", "stock_shares"]


async def test_hits_from_different_groups_of_one_class_are_merged_without_duplicates():
    client = synthetic(
        {
            "stock_ppif": [row("AAA", "stock_ppif"), row("BBB", "stock_ppif")],
            "stock_etf": [row("BBB", "stock_etf"), row("CCC", "stock_etf")],
        }
    )

    result = await search_securities(client, "q", "fund")

    assert secids(result) == ["AAA", "BBB", "CCC"]
    assert {hit.asset_type for hit in result.results} == {"fund"}


# --- 7.2 расчётные показатели -----------------------------------------------------------------


async def test_calculated_indicators_are_not_returned(iss):
    result = await search_securities(iss, "TGLD", "any")

    assert "TGLD" in secids(result)
    assert not {"TGLDA", "TGLDB"} & set(secids(result))
    assert not {hit.board for hit in result.results} & {"INAV", "INPF"}


async def test_index_search_for_a_fund_ticker_finds_only_its_calculated_indicators_so_nothing(iss):
    result = await search_securities(iss, "TGLD", "index")

    assert result.results == []
    assert result.count == 0


async def test_index_class_keeps_only_real_index_boards():
    client = synthetic(
        {
            "stock_index": [
                row("IMOEX", "stock_index", "SNDX"),
                row("IMOEXCNY", "stock_index", "RTSI"),
                row("TMOSA", "stock_index", "INAV"),
                row("FIXSBER", "stock_index", "INPF"),
                row("WEIRD", "stock_index", "XXXX"),
            ]
        }
    )

    result = await search_securities(client, "moex", "index")

    assert secids(result) == ["IMOEX", "IMOEXCNY"]
    assert {hit.asset_type for hit in result.results} == {"index"}


# --- 7.3 порядок, лимит, пустой результат -----------------------------------------------------


async def test_exact_ticker_match_goes_first_ignoring_case(iss):
    result = await search_securities(iss, "sber", "any")

    assert secids(result)[0] == "SBER"


async def test_exact_isin_match_goes_first(iss):
    result = await search_securities(iss, "RU0009029540", "any")

    assert secids(result)[0] == "SBER"
    assert result.results[0].isin == "RU0009029540"


async def test_exact_match_is_moved_up_from_the_middle_of_iss_order():
    client = synthetic({"stock_shares": [row("AAA"), row("BBB"), row("TARGET", isin="RU0000000001"), row("CCC")]})

    by_ticker = await search_securities(client, "target", "stock")
    by_isin = await search_securities(client, "ru0000000001", "stock")

    assert secids(by_ticker) == ["TARGET", "AAA", "BBB", "CCC"]
    assert secids(by_isin) == ["TARGET", "AAA", "BBB", "CCC"]


async def test_limit_cuts_results_and_sets_truncated(iss):
    result = await search_securities(iss, "банк", "any", limit=5)

    assert len(result.results) == 5
    assert result.count == 5
    assert result.truncated is True


async def test_not_truncated_when_everything_fits():
    client = synthetic({"stock_shares": [row("AAA"), row("BBB")]})

    result = await search_securities(client, "q", "stock", limit=10)

    assert result.count == 2
    assert result.truncated is False


async def test_truncated_when_iss_itself_returned_a_full_page():
    client = synthetic({"stock_bonds": [row(f"B{i}", "stock_bonds", "TQCB") for i in range(ISS_SEARCH_LIMIT)]})

    result = await search_securities(client, "q", "bond", limit=20)

    assert result.count == 20
    assert result.truncated is True


async def test_nothing_found_is_a_normal_empty_result(iss):
    result = await search_securities(iss, "zzzqqq", "any")

    assert result.results == []
    assert result.count == 0
    assert result.truncated is False


async def test_blank_query_is_rejected(iss):
    with pytest.raises(InvalidArguments, match="query"):
        await search_securities(iss, "   ")


async def test_query_is_stripped(iss):
    result = await search_securities(iss, "  sber  ", "any")

    assert result.query == "sber"
    assert secids(result)[0] == "SBER"


def test_description_guides_the_agent():
    for phrase in ("get_current_price", "get_price_history", "asset_type", "truncated", "SBERP"):
        assert phrase in DESCRIPTION
