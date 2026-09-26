"""Точка входа MCP-сервера: регистрация инструментов, транспорты, запуск."""

import argparse
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .errors import MoexError
from .iss import IssClient
from .models import (
    AckResult,
    ChatIdParam,
    DateFromParam,
    DateTillParam,
    HistoryResult,
    IntervalParam,
    LimitParam,
    PollIntervalParam,
    PriceResult,
    QueryParam,
    Report,
    ReportIdsParam,
    ReportIntervalParam,
    RunDueResult,
    SearchAssetTypeParam,
    SearchResult,
    SecidParam,
    WatchSecidsParam,
    WatchSetResult,
    WatchStatusResult,
    WatchStopResult,
)
from .tools import history, price, search
from .tools import watch as watch_tools
from .watch.service import WatchService
from .watch.store import WatchStore

logger = logging.getLogger(__name__)

SERVER_NAME = "mcp-moex"
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000

INSTRUCTIONS = (
    "Данные Московской биржи (ISS): акции, облигации, фонды, индексы. Порядок работы: если бумага названа "
    "словами или ISIN, найдите тикер через search_securities; затем get_current_price для цены и "
    "get_price_history для истории. Котировки акций, облигаций и фондов задержаны на 15 минут."
)

WATCH_INSTRUCTIONS = (
    " Включён режим расписания: инструменты watch_* служебные, они для клиента-планировщика, а не для ответов "
    "пользователю."
)

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
"""Инструменты чтения только читают публичные данные биржи."""

# Инструменты расписания НЕ помечены read_only_hint: клиент отдаёт модели только инструменты с этим признаком,
# а эти принимают chat_id извне и изменяют хранилище (даже watch_status и watch_get_report не для модели).
WATCH_MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)
WATCH_LOCAL = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
WATCH_LOCAL_READ = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

_T = TypeVar("_T")


async def _guard(call: Awaitable[_T]) -> _T:
    """Доменную ошибку отдаём агенту её текстом; непредвиденные сбои SDK превращает в общую ошибку без деталей."""
    try:
        return await call
    except MoexError as error:
        raise ToolError(str(error)) from error


def create_server(
    iss: IssClient | None = None,
    watch_db: str | Path | None = None,
    clock: Callable[[], float] | None = None,
) -> MCPServer:
    """Собирает сервер. Без `iss` использует настоящий клиент ISS и закрывает его при остановке.

    С `watch_db` (путь к файлу SQLite) дополнительно публикует шесть инструментов расписания; без него сервер
    ровно такой, как раньше: три инструмента чтения и никаких файлов. `clock` (секунды Unix) нужен тестам.
    """
    client = iss if iss is not None else IssClient()
    owns_client = iss is None

    @asynccontextmanager
    async def lifespan(_: MCPServer):
        try:
            yield None
        finally:
            if owns_client:
                await client.aclose()

    instructions = INSTRUCTIONS + (WATCH_INSTRUCTIONS if watch_db is not None else "")
    server = MCPServer(SERVER_NAME, instructions=instructions, lifespan=lifespan)

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

    if watch_db is not None:
        _register_watch_tools(server, WatchService(WatchStore(watch_db), client, clock or time.time))

    return server


def _register_watch_tools(server: MCPServer, service: WatchService) -> None:
    @server.tool(name="watch_set", description=watch_tools.SET_DESCRIPTION, annotations=WATCH_MUTATING)
    async def watch_set(
        chat_id: ChatIdParam,
        secids: WatchSecidsParam,
        poll_interval: PollIntervalParam,
        report_interval: ReportIntervalParam,
    ) -> WatchSetResult:
        return await _guard(watch_tools.watch_set(service, chat_id, secids, poll_interval, report_interval))

    @server.tool(name="watch_stop", description=watch_tools.STOP_DESCRIPTION, annotations=WATCH_LOCAL)
    async def watch_stop(chat_id: ChatIdParam) -> WatchStopResult:
        return await _guard(watch_tools.watch_stop(service, chat_id))

    @server.tool(name="watch_status", description=watch_tools.STATUS_DESCRIPTION, annotations=WATCH_LOCAL_READ)
    async def watch_status(chat_id: ChatIdParam) -> WatchStatusResult:
        return await _guard(watch_tools.watch_status(service, chat_id))

    @server.tool(name="watch_get_report", description=watch_tools.GET_REPORT_DESCRIPTION, annotations=WATCH_LOCAL_READ)
    async def watch_get_report(chat_id: ChatIdParam) -> Report:
        return await _guard(watch_tools.watch_get_report(service, chat_id))

    @server.tool(name="watch_run_due", description=watch_tools.RUN_DUE_DESCRIPTION, annotations=WATCH_MUTATING)
    async def watch_run_due() -> RunDueResult:
        return await _guard(watch_tools.watch_run_due(service))

    @server.tool(name="watch_ack", description=watch_tools.ACK_DESCRIPTION, annotations=WATCH_LOCAL)
    async def watch_ack(report_ids: ReportIdsParam) -> AckResult:
        return await _guard(watch_tools.watch_ack(service, report_ids))


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
    parser.add_argument(
        "--watch-db",
        type=Path,
        default=None,
        metavar="ПУТЬ",
        help=(
            "включить режим расписания (инструменты watch_* для клиента-планировщика): путь к файлу SQLite "
            "с замерами и сводками, создаётся при первом использовании; без флага файлов на диске нет"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    configure_logging()
    server = create_server(watch_db=args.watch_db)
    run_kwargs: dict[str, Any] = {}
    if args.transport == "streamable-http":
        run_kwargs = {"host": LOOPBACK_HOST, "port": args.port}
        logger.info("Streamable HTTP на http://%s:%d/mcp", LOOPBACK_HOST, args.port)
    server.run(args.transport, **run_kwargs)


if __name__ == "__main__":
    main()
