import httpx
import pytest

from mcp_moex.errors import IssUnavailable
from mcp_moex.iss import IssClient, parse_tables

# Ответ ISS, записанный с живого сервера (SBER, TQBR), в виде, как его отдаёт iss.moex.com.
SBER_MARKETDATA = {
    "marketdata": {
        "columns": ["SECID", "BOARDID", "LAST", "LCURRENTPRICE", "SYSTIME", "TIME", "TRADINGSTATUS"],
        "data": [["SBER", "TQBR", 279.95, 279.96, "2026-09-24 18:11:36", "17:56:35", "T"]],
    }
}

SBER_PATH = "engines/stock/markets/shares/boards/TQBR/securities/SBER.json"


async def no_sleep(_: float) -> None:
    return None


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_client(handler, **kwargs) -> IssClient:
    kwargs.setdefault("sleep", no_sleep)
    return IssClient(transport=httpx.MockTransport(handler), **kwargs)


# --- 2.1 разбор таблиц и запрос ---------------------------------------------------------------


def test_parse_tables_turns_columns_and_data_into_dicts():
    tables = parse_tables(SBER_MARKETDATA)

    assert tables == {
        "marketdata": [
            {
                "SECID": "SBER",
                "BOARDID": "TQBR",
                "LAST": 279.95,
                "LCURRENTPRICE": 279.96,
                "SYSTIME": "2026-09-24 18:11:36",
                "TIME": "17:56:35",
                "TRADINGSTATUS": "T",
            }
        ]
    }


def test_parse_tables_keeps_empty_tables_and_ignores_non_tables():
    payload = {"boards": {"columns": ["secid"], "data": []}, "note": "not a table"}

    assert parse_tables(payload) == {"boards": []}


async def test_get_tables_requests_iss_and_returns_parsed_tables():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SBER_MARKETDATA)

    client = make_client(handler)
    tables = await client.get_tables(SBER_PATH, {"iss.only": "marketdata"})

    assert tables["marketdata"][0]["LAST"] == 279.95
    request = seen[0]
    assert str(request.url).startswith(f"https://iss.moex.com/iss/{SBER_PATH}")
    assert request.url.params["iss.meta"] == "off"
    assert request.url.params["iss.only"] == "marketdata"
    await client.aclose()


# --- 2.2 повторы и IssUnavailable -------------------------------------------------------------


async def test_retries_transient_failures_and_then_succeeds():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectTimeout("timeout", request=request)
        if attempts == 2:
            return httpx.Response(503)
        return httpx.Response(200, json=SBER_MARKETDATA)

    client = make_client(handler, retries=2)
    tables = await client.get_tables(SBER_PATH)

    assert attempts == 3
    assert tables["marketdata"][0]["SECID"] == "SBER"
    await client.aclose()


@pytest.mark.parametrize("failure", ["timeout", "503", "429"])
async def test_raises_iss_unavailable_after_retries_are_exhausted(failure):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(int(failure))

    client = make_client(handler, retries=2)
    with pytest.raises(IssUnavailable) as error:
        await client.get_tables(SBER_PATH)

    assert attempts == 3
    assert "повторите" in str(error.value).lower()
    await client.aclose()


async def test_client_error_is_not_retried():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404)

    client = make_client(handler, retries=2)
    with pytest.raises(IssUnavailable, match="HTTP 404"):
        await client.get_tables("nope.json")

    assert attempts == 1
    await client.aclose()


async def test_non_json_response_is_iss_unavailable():
    client = make_client(lambda request: httpx.Response(200, text="<html>maintenance</html>"))

    with pytest.raises(IssUnavailable, match="не является JSON"):
        await client.get_tables(SBER_PATH)
    await client.aclose()


# --- 2.3 кэш ----------------------------------------------------------------------------------


async def test_cache_serves_repeated_request_within_ttl_and_refetches_after():
    calls = 0
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=SBER_MARKETDATA)

    client = make_client(handler, clock=clock)

    await client.get_tables(SBER_PATH, ttl=10)
    await client.get_tables(SBER_PATH, ttl=10)
    assert calls == 1

    clock.now += 9.9
    await client.get_tables(SBER_PATH, ttl=10)
    assert calls == 1

    clock.now += 0.2
    await client.get_tables(SBER_PATH, ttl=10)
    assert calls == 2
    await client.aclose()


async def test_cache_is_keyed_by_parameters_and_disabled_without_ttl():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=SBER_MARKETDATA)

    client = make_client(handler)

    await client.get_tables(SBER_PATH, {"a": 1}, ttl=10)
    await client.get_tables(SBER_PATH, {"a": 2}, ttl=10)
    assert calls == 2

    await client.get_tables(SBER_PATH, {"a": 1})
    await client.get_tables(SBER_PATH, {"a": 1})
    assert calls == 4
    await client.aclose()


async def test_failed_request_is_not_cached():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 3:
            return httpx.Response(503)
        return httpx.Response(200, json=SBER_MARKETDATA)

    client = make_client(handler, retries=2)
    with pytest.raises(IssUnavailable):
        await client.get_tables(SBER_PATH, ttl=10)

    tables = await client.get_tables(SBER_PATH, ttl=10)
    assert tables["marketdata"]
    await client.aclose()
