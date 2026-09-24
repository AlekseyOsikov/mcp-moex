"""Записывает эталонные ответы ISS в `tests/fixtures/` (нужна сеть): `uv run python -m tests.record_fixtures`.

Уже записанные файлы не перезаписываются; чтобы обновить фикстуру, удалите её файл.
"""

import asyncio
import os

from mcp_moex.iss import IssClient
from mcp_moex.tools.search import ISS_SEARCH_LIMIT, SEARCH_COLUMNS

from .fake_iss import RECORD_ENV, FixtureTransport

SECURITY_GROUPS = ("stock_shares", "stock_dr", "stock_bonds", "stock_ppif", "stock_etf", "stock_index")
SEARCH_QUERIES = ("Сбербанк", "золото", "ОФЗ 26238", "TGLD", "sber", "RU0009029540", "банк", "zzzqqq")

# (engine, market, board, secid)
QUOTED = (
    ("stock", "shares", "TQBR", "SBER"),
    ("stock", "shares", "TQBR", "TMOS"),
    ("stock", "bonds", "TQOB", "SU26238RMFS4"),
    ("stock", "index", "SNDX", "IMOEX"),
    ("stock", "index", "RTSI", "IMOEXCNY"),
)
CANDLED = (
    ("stock", "shares", "TQBR", "SBER"),
    ("stock", "bonds", "TQOB", "SU26238RMFS4"),
    ("stock", "index", "SNDX", "IMOEX"),
    ("stock", "index", "RTSI", "IMOEXCNY"),
)
SECURITY_CARDS = ("SBER", "sber", "SU26238RMFS4", "TMOS", "IMOEX", "NOPE123", "SiZ6", "IMOEXCNY")


def recordings() -> list[tuple[str, dict]]:
    requests: list[tuple[str, dict]] = []
    for secid in SECURITY_CARDS:
        requests.append((f"securities/{secid}.json", {"iss.only": "description,boards"}))
    for engine, market, board, secid in QUOTED:
        path = f"engines/{engine}/markets/{market}/boards/{board}/securities/{secid}.json"
        requests.append((path, {"iss.only": "marketdata,securities"}))
    for engine, market, board, secid in CANDLED:
        path = f"engines/{engine}/markets/{market}/boards/{board}/securities/{secid}/candles.json"
        requests.append((path, {"from": "2026-09-01", "till": "2026-09-10", "interval": 24, "start": 0}))
    for query in SEARCH_QUERIES:
        for group in SECURITY_GROUPS:
            requests.append(
                (
                    "securities.json",
                    {
                        "q": query,
                        "is_trading": 1,
                        "group_by": "group",
                        "group_by_filter": group,
                        "limit": ISS_SEARCH_LIMIT,
                        "iss.only": "securities",
                        "securities.columns": SEARCH_COLUMNS,
                    },
                )
            )
    return requests


async def main() -> None:
    os.environ[RECORD_ENV] = "1"
    client = IssClient(transport=FixtureTransport())
    requests = recordings()
    for path, params in requests:
        await client.get_tables(path, params)
        await asyncio.sleep(0.15)
    await client.aclose()
    print(f"Готово: {len(requests)} запросов записано или найдено в tests/fixtures/")


if __name__ == "__main__":
    asyncio.run(main())
