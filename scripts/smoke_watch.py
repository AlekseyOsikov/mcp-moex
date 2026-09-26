"""Смоук-клиент режима расписания: запускает сервер с `--watch-db` во временной БД и вызывает инструменты `watch_*`.

Запуск из корня проекта:  uv run python scripts/smoke_watch.py

Нужен живой ISS: при постановке опроса делается первый замер. Код возврата 0, если все шаги прошли; иначе 1,
а в stderr указан шаг, на котором сбой. Постоянных файлов скрипт не оставляет.
"""

import argparse
import asyncio
import shlex
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_client import (  # noqa: E402  (общие шаги и разбор результатов смоук-клиента)
    StepFailed,
    call_tool,
    default_server_command,
    first_step_failure,
    show,
    step,
)

WATCH_TOOLS = {"watch_set", "watch_stop", "watch_status", "watch_get_report", "watch_run_due", "watch_ack"}
CHAT_ID = 1
SECIDS = ["SBER", "IMOEX"]


async def run_steps(session: ClientSession) -> None:
    await step("initialize", session.initialize())

    tools = await step("list_tools", session.list_tools())
    names = {tool.name for tool in tools.tools}
    print("\n".join(f"- {name}" for name in sorted(names)))
    if missing := WATCH_TOOLS - names:
        raise StepFailed("list_tools", f"нет инструментов расписания: {sorted(missing)}")

    placed = await step(
        "watch_set",
        call_tool(
            session,
            "watch_set",
            {"chat_id": CHAT_ID, "secids": SECIDS, "poll_interval": "5m", "report_interval": "1h"},
        ),
    )
    show(placed)
    if placed["secids"] != SECIDS or len(placed["first_samples"]) != len(SECIDS):
        raise StepFailed("watch_set", f"ожидались тикеры {SECIDS} и по первому замеру на каждый")

    due = await step("watch_run_due", call_tool(session, "watch_run_due", {}))
    show(due)
    if due["next_due_at"] is None:
        raise StepFailed("watch_run_due", "при активном опросе next_due_at не должен быть null")

    report = await step("watch_get_report", call_tool(session, "watch_get_report", {"chat_id": CHAT_ID}))
    print(report["text"])
    if [ticker["secid"] for ticker in report["tickers"]] != SECIDS or report["report_id"] is not None:
        raise StepFailed("watch_get_report", "сводка по запросу должна содержать оба тикера и report_id = null")
    if any(ticker["samples"] < 1 for ticker in report["tickers"]):
        raise StepFailed("watch_get_report", "по тикеру нет ни одного замера, хотя первый сделан при постановке")

    status = await step("watch_status", call_tool(session, "watch_status", {"chat_id": CHAT_ID}))
    show(status)
    if status.get("active") is not True:
        raise StepFailed("watch_status", "опрос должен быть активен")

    stopped = await step("watch_stop", call_tool(session, "watch_stop", {"chat_id": CHAT_ID}))
    show(stopped)
    if stopped["stopped"] is not True:
        raise StepFailed("watch_stop", "опрос был задан, stopped должен быть true")

    after = await step("watch_status после остановки", call_tool(session, "watch_status", {"chat_id": CHAT_ID}))
    if after != {"active": False}:
        raise StepFailed("watch_status после остановки", f"ожидалось {{'active': False}}, получено {after}")


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
        failure = first_step_failure(group)
        if failure is not None:
            raise failure from group
        raise StepFailed("запуск сервера", "; ".join(f"{type(e).__name__}: {e}" for e in group.exceptions)) from group
    except Exception as error:
        raise StepFailed("запуск сервера", f"{type(error).__name__}: {error}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Смоук-проверка режима расписания mcp-moex.")
    parser.add_argument(
        "--server-command",
        default=None,
        help="команда запуска сервера целиком, с --watch-db (по умолчанию: uv run --directory <проект> mcp-moex "
        "--watch-db <временный файл>)",
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="mcp-moex-watch-") as tmp:
        command = (
            shlex.split(args.server_command)
            if args.server_command
            else [*default_server_command(), "--watch-db", str(Path(tmp) / "watch.db")]
        )
        try:
            asyncio.run(run(command))
        except StepFailed as failure:
            print(f"\nОШИБКА на шаге «{failure.step}»: {failure.reason}", file=sys.stderr)
            return 1
    print("\nOK: все шаги выполнены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
