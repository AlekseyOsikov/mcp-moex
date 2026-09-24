"""Сервер целиком: клиент MCP SDK подключается к нему в памяти и вызывает инструменты."""

import json

import httpx
import jsonschema
import pytest
from mcp import Client

from mcp_moex.iss import IssClient
from mcp_moex.server import create_server

from .fake_iss import RECORD_ENV, FixtureTransport

TOOLS = {"search_securities", "get_current_price", "get_price_history"}
BOND_FIELDS = {"face_value", "currency", "price_rub", "accrued_interest", "dirty_price", "yield_percent"}

CALLS = {
    "search_securities": {"query": "Сбербанк", "asset_type": "stock"},
    "get_current_price": {"secid": "SBER"},
    "get_price_history": {"secid": "SBER", "date_from": "2026-09-01", "date_till": "2026-09-10"},
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv(RECORD_ENV, raising=False)


def connect(iss: IssClient | None = None) -> Client:
    """Клиент SDK, подключённый к серверу в памяти. Открывать нужно в теле теста: контекст anyio привязан к задаче."""
    return Client(create_server(iss or IssClient(transport=FixtureTransport())))


async def tools_by_name(client: Client) -> dict:
    return {tool.name: tool for tool in (await client.list_tools()).tools}


# --- 8.1 регистрация инструментов -------------------------------------------------------------


async def test_list_tools_returns_exactly_the_three_tools():
    async with connect() as client:
        assert set(await tools_by_name(client)) == TOOLS


async def test_every_tool_is_described_and_read_only():
    async with connect() as client:
        for name, tool in (await tools_by_name(client)).items():
            assert tool.description and len(tool.description) > 100, name
            assert tool.annotations is not None, name
            assert tool.annotations.read_only_hint is True, name


async def test_every_input_parameter_is_described_in_the_schema():
    async with connect() as client:
        for name, tool in (await tools_by_name(client)).items():
            properties = tool.input_schema["properties"]
            assert properties, name
            for parameter, schema in properties.items():
                assert schema.get("description"), f"{name}.{parameter} без описания"


async def test_schemas_carry_types_bounds_enums_and_defaults():
    async with connect() as client:
        tools = await tools_by_name(client)

        search = tools["search_securities"].input_schema
        assert search["required"] == ["query"]
        assert search["properties"]["asset_type"]["enum"] == ["any", "stock", "bond", "fund", "index"]
        assert search["properties"]["asset_type"]["default"] == "any"
        assert (search["properties"]["limit"]["minimum"], search["properties"]["limit"]["maximum"]) == (1, 20)
        assert search["properties"]["limit"]["default"] == 10

        price = tools["get_current_price"].input_schema
        assert price["required"] == ["secid"]
        assert price["properties"]["secid"]["type"] == "string"

        history = tools["get_price_history"].input_schema
        assert set(history["required"]) == {"secid", "date_from"}
        assert history["properties"]["interval"]["enum"] == ["hour", "day", "week", "month"]
        assert history["properties"]["interval"]["default"] == "day"
        assert "pattern" in history["properties"]["date_from"]


async def test_price_and_history_descriptions_point_to_search():
    async with connect() as client:
        tools = await tools_by_name(client)

        for name in ("get_current_price", "get_price_history"):
            assert "search_securities" in tools[name].description, name


async def test_descriptions_state_delay_and_units():
    async with connect() as client:
        tools = await tools_by_name(client)

        price = tools["get_current_price"].description
        assert "15 минут" in price
        assert "percent_of_face" in price and "points" in price
        assert "не скорректированы" in tools["get_price_history"].description


# --- 8.2 результаты и ошибки ------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(TOOLS))
async def test_success_returns_structured_content_and_same_json_text(name):
    async with connect() as client:
        tool = (await tools_by_name(client))[name]

        result = await client.call_tool(name, CALLS[name])

        assert result.is_error is False
        assert result.structured_content
        assert json.loads(result.content[0].text) == result.structured_content
        jsonschema.validate(result.structured_content, tool.output_schema)


async def test_result_has_named_fields_and_no_raw_iss_tables():
    async with connect() as client:
        result = await client.call_tool("get_price_history", CALLS["get_price_history"])

        payload = result.structured_content
        assert {"summary", "candles", "price_unit", "interval"} <= payload.keys()
        assert "columns" not in payload and "data" not in payload
        assert {"begin", "open", "high", "low", "close", "volume", "turnover"} <= payload["candles"][0].keys()


async def test_stock_and_index_results_carry_no_bond_fields_but_bond_does():
    async with connect() as client:
        stock = (await client.call_tool("get_current_price", {"secid": "SBER"})).structured_content
        index = (await client.call_tool("get_current_price", {"secid": "IMOEX"})).structured_content
        bond = (await client.call_tool("get_current_price", {"secid": "SU26238RMFS4"})).structured_content

        assert not BOND_FIELDS & stock.keys()
        assert not BOND_FIELDS & index.keys()
        assert BOND_FIELDS <= bond.keys()
        assert bond["price_unit"] == "percent_of_face"


async def test_unknown_ticker_is_a_tool_error_with_a_recommendation():
    async with connect() as client:
        result = await client.call_tool("get_current_price", {"secid": "NOPE123"})

        assert result.is_error is True
        text = result.content[0].text
        assert "NOPE123" in text
        assert "search_securities" in text
        assert "Traceback" not in text


async def test_unsupported_class_is_a_tool_error_listing_supported_classes():
    async with connect() as client:
        result = await client.call_tool("get_price_history", {"secid": "SiZ6", "date_from": "2026-09-01"})

        assert result.is_error is True
        assert "акции" in result.content[0].text


async def test_invalid_arguments_are_a_tool_error_naming_the_parameter():
    async with connect() as client:
        result = await client.call_tool("get_current_price", {"secid": "bad ticker"})

        assert result.is_error is True
        assert "secid" in result.content[0].text

        missing = await client.call_tool("get_price_history", {"secid": "SBER"})
        assert missing.is_error is True
        assert "date_from" in missing.content[0].text

        blank = await client.call_tool("search_securities", {"query": "   "})
        assert blank.is_error is True
        assert "query" in blank.content[0].text


async def test_inverted_period_is_a_tool_error():
    async with connect() as client:
        result = await client.call_tool(
            "get_price_history", {"secid": "SBER", "date_from": "2026-09-10", "date_till": "2026-09-01"}
        )

        assert result.is_error is True
        assert "позже" in result.content[0].text


async def test_unexpected_crash_is_reported_without_details(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("секретная внутренняя деталь")

    async with connect() as client:
        with monkeypatch.context() as patched:
            patched.setattr("mcp_moex.tools.price.get_current_price", boom)
            result = await client.call_tool("get_current_price", {"secid": "SBER"})

        assert result.is_error is True
        assert "секретная внутренняя деталь" not in result.content[0].text
        assert "Traceback" not in result.content[0].text
        # сервер жив и отвечает на следующие вызовы
        assert (await client.call_tool("get_current_price", {"secid": "SBER"})).is_error is False


async def test_iss_outage_is_reported_and_server_keeps_working():
    outage = True
    fixtures = FixtureTransport()

    class Flaky(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if outage:
                raise httpx.ConnectTimeout("нет связи", request=request)
            return await fixtures.handle_async_request(request)

    async def no_sleep(_: float) -> None:
        return None

    iss = IssClient(transport=Flaky(), retries=1, sleep=no_sleep)
    async with Client(create_server(iss)) as client:
        down = await client.call_tool("get_current_price", {"secid": "SBER"})
        assert down.is_error is True
        assert "временно недоступна" in down.content[0].text
        assert "Повторите" in down.content[0].text

        outage = False
        up = await client.call_tool("get_current_price", {"secid": "SBER"})
        assert up.is_error is False
