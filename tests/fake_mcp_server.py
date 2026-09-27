"""Крошечный MCP-сервер с теми же шестью инструментами и управляемыми поломками (для тестов смоук-клиента).

Запуск: python tests/fake_mcp_server.py [--fail ИМЯ_ИНСТРУМЕНТА] [--without ИМЯ_ИНСТРУМЕНТА]
"""

import argparse
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_moex.models import (
    BenchmarkHistoryParam,
    Candle,
    HistoryAParam,
    HistoryBParam,
    HistoryParam,
    HistoryResult,
    HistorySummary,
    PriceResult,
    SearchResult,
    SecurityHit,
)

parser = argparse.ArgumentParser()
parser.add_argument("--fail", default=None, help="инструмент, который всегда возвращает ошибку")
parser.add_argument("--without", default=None, help="инструмент, который не регистрируется")
args = parser.parse_args()

server = MCPServer("fake-moex")


def failing(name: str) -> None:
    if args.fail == name:
        raise ToolError("имитация сбоя ISS: Московская биржа временно недоступна")


def register(name: str):
    return (lambda fn: fn) if args.without == name else server.tool(name=name, description=f"Фейковый {name}")


@register("search_securities")
def search_securities(query: str, asset_type: str = "any", limit: int = 10) -> SearchResult:
    failing("search_securities")
    hit = SecurityHit(secid="SBER", name="Сбербанк", isin="RU0009029540", asset_type="stock", board="TQBR")
    return SearchResult(query=query, asset_type="stock", results=[hit], count=1, truncated=False)


@register("get_current_price")
def get_current_price(secid: str) -> PriceResult:
    failing("get_current_price")
    return PriceResult(
        secid=secid,
        name="Сбербанк",
        asset_type="stock",
        price=280.0,
        price_unit="RUB",
        price_source="last_trade",
        as_of="2026-09-24T19:00:00+03:00",
        delayed=True,
        delay_minutes=15,
        trading_status="T",
    )


@register("get_price_history")
def get_price_history(secid: str, date_from: str, date_till: str | None = None, interval: str = "day") -> HistoryResult:
    failing("get_price_history")
    candle = Candle(begin="2026-09-24T00:00:00+03:00", open=1, high=2, low=0.5, close=1.5, volume=10, turnover=15.0)
    summary = HistorySummary(
        candles_count=1,
        start_price=1,
        end_price=1.5,
        min_price=0.5,
        max_price=2,
        change_abs=0.5,
        change_pct=50.0,
        first_candle=candle.begin,
        last_candle=candle.begin,
    )
    return HistoryResult(
        secid=secid,
        name="Сбербанк",
        asset_type="stock",
        price_unit="RUB",
        interval="day",
        date_from=date_from,
        date_till=date_till or "2026-09-24",
        summary=summary,
        candles=[candle],
        candles_returned=1,
        candles_truncated=False,
    )


# Инструменты анализа принимают историю в настоящей модели: заглушка проверяет, что клиент передал результат целиком.


@register("compute_price_metrics")
def compute_price_metrics(history: HistoryParam) -> dict[str, Any]:
    failing("compute_price_metrics")
    return {"secid": history.secid, "change_pct": history.summary.change_pct, "text": "метрики"}


@register("compare_with_benchmark")
def compare_with_benchmark(history: HistoryParam, benchmark_history: BenchmarkHistoryParam) -> dict[str, Any]:
    failing("compare_with_benchmark")
    return {"secid": history.secid, "benchmark_secid": benchmark_history.secid, "difference_pp": 0.0, "text": "сравнение"}


@register("compare_securities")
def compare_securities(history_a: HistoryAParam, history_b: HistoryBParam) -> dict[str, Any]:
    failing("compare_securities")
    return {"a_secid": history_a.secid, "b_secid": history_b.secid, "difference_pp": 0.0, "text": "сравнение"}


if __name__ == "__main__":
    server.run("stdio")
