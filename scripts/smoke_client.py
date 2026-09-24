"""Смоук-клиент: запускает сервер mcp-moex по stdio и вызывает все три инструмента на реальном ISS.

Запуск из корня проекта:  uv run python scripts/smoke_client.py

Код возврата 0, если все шаги прошли; иначе 1, а в stderr указан шаг, на котором сбой.
Скрипт же показывает, как подключиться к серверу из проекта агента (см. `connect` и README).
"""

import argparse
import asyncio
import json
import shlex
import sys
from collections.abc import Awaitable
from datetime import date, timedelta
from pathlib import Path
from typing import Any, TypeVar

from mcp import ClientSession, StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
STEP_TIMEOUT_SECONDS = 90.0
"""Первый запуск `uv run` может собирать окружение, поэтому таймаут щедрый."""

_T = TypeVar("_T")


class StepFailed(Exception):
    def __init__(self, step: str, reason: str) -> None:
        self.step = step
        self.reason = reason
        super().__init__(f"{step}: {reason}")


def default_server_command() -> list[str]:
    return ["uv", "run", "--directory", str(ROOT), "mcp-moex"]


def field(obj: Any, snake: str, camel: str) -> Any:
    """Поле результата MCP SDK: в mcp 2.x snake_case, в mcp 1.x camelCase."""
    return getattr(obj, snake, None) if hasattr(obj, snake) else getattr(obj, camel, None)


async def step(name: str, call: Awaitable[_T]) -> _T:
    """Выполняет шаг с таймаутом; любой сбой превращает в StepFailed с названием шага."""
    print(f"\n== {name} ==", flush=True)
    try:
        async with asyncio.timeout(STEP_TIMEOUT_SECONDS):
            return await call
    except TimeoutError:
        raise StepFailed(name, f"нет ответа за {STEP_TIMEOUT_SECONDS:.0f} с") from None
    except StepFailed:
        raise
    except Exception as error:
        raise StepFailed(name, f"{type(error).__name__}: {error}") from error


async def call_tool(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Вызывает инструмент и возвращает его результат как словарь (structuredContent либо разбор текста)."""
    result = await session.call_tool(name, arguments)
    content = getattr(result, "content", None) or []
    text = content[0].text if content and hasattr(content[0], "text") else ""
    if field(result, "is_error", "isError"):
        raise StepFailed(name, f"инструмент вернул ошибку: {text}")
    structured = field(result, "structured_content", "structuredContent")
    if structured:
        return structured
    try:
        return json.loads(text)
    except ValueError:
        raise StepFailed(name, f"результат не JSON: {text[:200]!r}") from None


def show(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


async def run_steps(session: ClientSession) -> None:
    await step("initialize", session.initialize())

    tools = await step("list_tools", session.list_tools())
    for tool in tools.tools:
        first_line = (tool.description or "").strip().splitlines()[0]
        print(f"- {tool.name}: {first_line}")
    names = {tool.name for tool in tools.tools}
    expected = {"search_securities", "get_current_price", "get_price_history"}
    if names != expected:
        raise StepFailed("list_tools", f"ожидались инструменты {sorted(expected)}, получены {sorted(names)}")

    found = await step("search_securities", call_tool(session, "search_securities", {"query": "Сбербанк", "asset_type": "stock"}))
    show({"count": found["count"], "truncated": found["truncated"], "results": found["results"][:3]})
    if not found["results"]:
        raise StepFailed("search_securities", "по запросу «Сбербанк» ничего не найдено")
    secid = found["results"][0]["secid"]  # то, что сделал бы агент: найденный тикер идёт в остальные инструменты

    price = await step("get_current_price", call_tool(session, "get_current_price", {"secid": secid}))
    show(price)

    date_from = (date.today() - timedelta(days=30)).isoformat()
    history = await step(
        "get_price_history",
        call_tool(session, "get_price_history", {"secid": secid, "date_from": date_from, "interval": "day"}),
    )
    show(
        {
            "secid": history["secid"],
            "price_unit": history["price_unit"],
            "summary": history["summary"],
            "candles_returned": history["candles_returned"],
            "first_candle": history["candles"][0] if history["candles"] else None,
            "last_candle": history["candles"][-1] if history["candles"] else None,
        }
    )
    if not history["summary"]:
        raise StepFailed("get_price_history", f"за 30 дней с {date_from} нет свечей по {secid}")


def first_step_failure(group: BaseExceptionGroup) -> StepFailed | None:
    """Первый StepFailed из (возможно вложенной) группы исключений."""
    for error in group.exceptions:
        if isinstance(error, StepFailed):
            return error
        if isinstance(error, BaseExceptionGroup) and (nested := first_step_failure(error)) is not None:
            return nested
    return None


async def run(command: list[str]) -> None:
    params = StdioServerParameters(command=command[0], args=command[1:])
    print(f"Запускаю сервер: {shlex.join(command)}", flush=True)
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await run_steps(session)
    except StepFailed:
        raise
    except BaseExceptionGroup as group:
        # Сбой шага внутри task group SDK приходит завёрнутым в группу: достаём его, чтобы назвать шаг.
        failure = first_step_failure(group)
        if failure is not None:
            raise failure from group
        raise StepFailed("запуск сервера", "; ".join(f"{type(e).__name__}: {e}" for e in group.exceptions)) from group
    except Exception as error:
        raise StepFailed("запуск сервера", f"{type(error).__name__}: {error}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Смоук-проверка MCP-сервера mcp-moex.")
    parser.add_argument(
        "--server-command",
        default=None,
        help="команда запуска сервера (по умолчанию: uv run --directory <проект> mcp-moex)",
    )
    args = parser.parse_args(argv)
    command = shlex.split(args.server_command) if args.server_command else default_server_command()

    try:
        asyncio.run(run(command))
    except StepFailed as failure:
        print(f"\nОШИБКА на шаге «{failure.step}»: {failure.reason}", file=sys.stderr)
        return 1
    print("\nOK: все шаги выполнены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
