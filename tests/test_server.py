"""Сервер целиком: клиент MCP SDK подключается к нему в памяти и вызывает инструменты."""

import json

import httpx
import jsonschema
import pytest
from mcp import Client

from mcp_moex.iss import IssClient
from mcp_moex.server import create_server

from .fake_iss import RECORD_ENV, FixtureTransport
from .watch_helpers import Clock

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


# --- режим расписания ----------------------------------------------------------------------------

WATCH_TOOLS = {"watch_set", "watch_stop", "watch_status", "watch_get_report", "watch_run_due", "watch_ack"}
WATCH_SET_ARGS = {"chat_id": 1, "secids": ["sber", "TMOS"], "poll_interval": "15m", "report_interval": "1h"}


def connect_watch(path, clock=None) -> Client:
    return Client(create_server(IssClient(transport=FixtureTransport()), watch_db=path, clock=clock))


async def test_without_the_flag_there_are_exactly_three_tools_and_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async with connect() as client:
        assert set(await tools_by_name(client)) == TOOLS

    assert list(tmp_path.iterdir()) == []


async def test_with_the_flag_there_are_nine_tools_three_read_only_and_six_not(tmp_path):
    async with connect_watch(tmp_path / "watch.db") as client:
        tools = await tools_by_name(client)

        assert set(tools) == TOOLS | WATCH_TOOLS
        for name in TOOLS:
            assert tools[name].annotations.read_only_hint is True, name
        for name in WATCH_TOOLS:
            assert tools[name].annotations is not None, name
            assert tools[name].annotations.read_only_hint is not True, name


async def test_watch_tools_are_described_as_service_tools_and_parameters_are_documented(tmp_path):
    async with connect_watch(tmp_path / "watch.db") as client:
        tools = await tools_by_name(client)

        for name in WATCH_TOOLS:
            assert "клиента-планировщика" in tools[name].description, name
            for parameter, schema in tools[name].input_schema.get("properties", {}).items():
                assert schema.get("description"), f"{name}.{parameter} без описания"
        assert not tools["watch_run_due"].input_schema.get("properties")
        assert set(tools["watch_set"].input_schema["required"]) == {
            "chat_id", "secids", "poll_interval", "report_interval",
        }  # fmt: skip
        assert tools["watch_set"].input_schema["properties"]["chat_id"]["type"] == "integer"


async def test_idempotent_hints_only_where_repeating_does_not_change_state(tmp_path):
    async with connect_watch(tmp_path / "watch.db") as client:
        tools = await tools_by_name(client)

        for name in ("watch_stop", "watch_status", "watch_get_report", "watch_ack"):
            assert tools[name].annotations.idempotent_hint is True, name
        for name in ("watch_set", "watch_run_due"):
            assert tools[name].annotations.idempotent_hint is not True, name


async def test_the_storage_file_appears_only_on_first_watch_call(tmp_path):
    path = tmp_path / "sub" / "watch.db"
    async with connect_watch(path) as client:
        await client.list_tools()
        assert not path.exists()

        result = await client.call_tool("watch_status", {"chat_id": 1})

        assert result.is_error is False
        assert path.exists()


async def test_watch_scenario_through_the_client_on_the_fake_iss(tmp_path):
    clock = Clock()
    async with connect_watch(tmp_path / "watch.db", clock) as client:
        tools = await tools_by_name(client)

        def valid(name: str, result) -> dict:
            assert result.is_error is False, result.content[0].text
            assert json.loads(result.content[0].text) == result.structured_content
            jsonschema.validate(result.structured_content, tools[name].output_schema)
            return result.structured_content

        placed = valid("watch_set", await client.call_tool("watch_set", WATCH_SET_ARGS))
        assert placed["secids"] == ["SBER", "TMOS"] and placed["replaced"] is False
        assert len(placed["first_samples"]) == 2

        status = valid("watch_status", await client.call_tool("watch_status", {"chat_id": 1}))
        assert status["active"] is True and status["samples_in_period"] == 2

        clock.advance(3600)
        due = valid("watch_run_due", await client.call_tool("watch_run_due", {}))
        (report,) = due["reports"]
        assert due["has_more"] is False and due["next_due_at"].endswith("+03:00")
        assert "Сводка за" in report["text"] and "SBER" in report["text"]

        preview = valid("watch_get_report", await client.call_tool("watch_get_report", {"chat_id": 1}))
        assert preview["report_id"] is None

        acked = valid("watch_ack", await client.call_tool("watch_ack", {"report_ids": [report["report_id"]]}))
        assert acked == {"acknowledged": 1}

        stopped = valid("watch_stop", await client.call_tool("watch_stop", {"chat_id": 1}))
        assert stopped == {"chat_id": 1, "stopped": True}
        assert valid("watch_status", await client.call_tool("watch_status", {"chat_id": 1})) == {"active": False}


async def test_watch_errors_are_tool_errors_with_russian_text(tmp_path):
    async with connect_watch(tmp_path / "watch.db") as client:
        unknown = await client.call_tool("watch_set", {**WATCH_SET_ARGS, "secids": ["NOPE123"]})
        assert unknown.is_error is True
        assert "NOPE123" in unknown.content[0].text and "search_securities" in unknown.content[0].text

        too_frequent = await client.call_tool("watch_set", {**WATCH_SET_ARGS, "poll_interval": "1m"})
        assert too_frequent.is_error is True
        assert "poll_interval" in too_frequent.content[0].text and "5m" in too_frequent.content[0].text

        too_many = await client.call_tool("watch_set", {**WATCH_SET_ARGS, "secids": [f"T{i}" for i in range(11)]})
        assert too_many.is_error is True and "10" in too_many.content[0].text

        no_watch = await client.call_tool("watch_get_report", {"chat_id": 5})
        assert no_watch.is_error is True and "опрос не задан" in no_watch.content[0].text

        bad_chat = await client.call_tool("watch_status", {"chat_id": "abc"})
        assert bad_chat.is_error is True and "chat_id" in bad_chat.content[0].text

        for result in (unknown, too_frequent, too_many, no_watch):
            assert "Traceback" not in result.content[0].text


async def test_unusable_storage_is_a_tool_error_and_read_tools_keep_working(tmp_path):
    broken = tmp_path / "watch.db"
    broken.write_bytes(b"not a database" * 200)
    async with connect_watch(broken) as client:
        result = await client.call_tool("watch_status", {"chat_id": 1})

        assert result.is_error is True
        assert "--watch-db" in result.content[0].text
        assert "Traceback" not in result.content[0].text
        assert (await client.call_tool("get_current_price", {"secid": "SBER"})).is_error is False
