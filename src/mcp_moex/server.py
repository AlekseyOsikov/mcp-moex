"""Точка входа MCP-сервера: регистрация инструментов, транспорты, запуск."""

import argparse
import logging
import sys
from collections.abc import Awaitable, Sequence
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .errors import MoexError
from .iss import IssClient
from .models import (
    DateFromParam,
    DateTillParam,
    HistoryResult,
    IntervalParam,
    LimitParam,
    PriceResult,
    QueryParam,
    SearchAssetTypeParam,
    SearchResult,
    SecidParam,
)
from .tools import history, price, search

logger = logging.getLogger(__name__)

SERVER_NAME = "mcp-moex"
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000

INSTRUCTIONS = (
    "Данные Московской биржи (ISS): акции, облигации, фонды, индексы. Порядок работы: если бумага названа "
    "словами или ISIN, найдите тикер через search_securities; затем get_current_price для цены и "
    "get_price_history для истории. Котировки акций, облигаций и фондов задержаны на 15 минут."
)

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
"""Все инструменты только читают публичные данные биржи."""

_T = TypeVar("_T")


async def _guard(call: Awaitable[_T]) -> _T:
    """Доменную ошибку отдаём агенту её текстом; непредвиденные сбои SDK превращает в общую ошибку без деталей."""
    try:
        return await call
    except MoexError as error:
        raise ToolError(str(error)) from error


def create_server(iss: IssClient | None = None) -> MCPServer:
    """Собирает сервер. Без аргумента использует настоящий клиент ISS и закрывает его при остановке."""
    client = iss if iss is not None else IssClient()
    owns_client = iss is None

    @asynccontextmanager
    async def lifespan(_: MCPServer):
        try:
            yield None
        finally:
            if owns_client:
                await client.aclose()

    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, lifespan=lifespan)

    @server.tool(name="search_securities", description=search.DESCRIPTION, annotations=READ_ONLY)
    async def search_securities(
        query: QueryParam,
        asset_type: SearchAssetTypeParam = "any",
        limit: LimitParam = 10,
    ) -> SearchResult:
        return await _guard(search.search_securities(client, query, asset_type, limit))

    @server.tool(name="get_current_price", description=price.DESCRIPTION, annotations=READ_ONLY)
    async def get_current_price(secid: SecidParam) -> PriceResult:
        return await _guard(price.get_current_price(client, secid))

    @server.tool(name="get_price_history", description=history.DESCRIPTION, annotations=READ_ONLY)
    async def get_price_history(
        secid: SecidParam,
        date_from: DateFromParam,
        date_till: DateTillParam = None,
        interval: IntervalParam = "day",
    ) -> HistoryResult:
        return await _guard(history.get_price_history(client, secid, date_from, date_till, interval))

    return server


def configure_logging() -> None:
    """Журнал только в stderr: по stdio в stdout идёт протокол, любой посторонний вывод его ломает."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # иначе каждый запрос к ISS попадает в журнал


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-moex",
        description="MCP-сервер данных Московской биржи (ISS). По умолчанию работает по stdio.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="stdio (по умолчанию: клиент сам запускает сервер) или streamable-http на 127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_HTTP_PORT,
        help=f"порт для streamable-http (по умолчанию {DEFAULT_HTTP_PORT}); адрес всегда {LOOPBACK_HOST}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    configure_logging()
    server = create_server()
    run_kwargs: dict[str, Any] = {}
    if args.transport == "streamable-http":
        run_kwargs = {"host": LOOPBACK_HOST, "port": args.port}
        logger.info("Streamable HTTP на http://%s:%d/mcp", LOOPBACK_HOST, args.port)
    server.run(args.transport, **run_kwargs)


if __name__ == "__main__":
    main()
