"""Запуск сервера: выбор транспорта, привязка к loopback и чистота stdout по stdio."""

import ast
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from mcp_moex import server as server_module

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "mcp_moex"


class FakeServer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.created_with: list[dict] = []

    def run(self, transport: str, **kwargs) -> None:
        self.calls.append((transport, kwargs))


@pytest.fixture
def fake_server(monkeypatch) -> FakeServer:
    fake = FakeServer()

    def create(**kwargs):
        fake.created_with.append(kwargs)
        return fake

    monkeypatch.setattr(server_module, "create_server", create)
    monkeypatch.setattr(server_module, "configure_logging", lambda: None)
    return fake


# --- 8.3 транспорты ---------------------------------------------------------------------------


def test_stdio_is_the_default_transport_and_opens_no_network_settings(fake_server):
    server_module.main([])

    assert fake_server.calls == [("stdio", {})]


def test_http_transport_binds_only_to_loopback(fake_server):
    server_module.main(["--transport", "streamable-http", "--port", "9123"])

    assert fake_server.calls == [("streamable-http", {"host": "127.0.0.1", "port": 9123})]


def test_http_transport_has_a_default_port_and_no_way_to_choose_another_host(fake_server):
    server_module.main(["--transport", "streamable-http"])

    transport, kwargs = fake_server.calls[0]
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == server_module.DEFAULT_HTTP_PORT
    with pytest.raises(SystemExit):
        server_module.main(["--transport", "streamable-http", "--host", "0.0.0.0"])


def test_unknown_transport_is_rejected(fake_server):
    with pytest.raises(SystemExit):
        server_module.main(["--transport", "sse"])


def test_watch_mode_is_off_unless_the_flag_is_given(fake_server):
    server_module.main([])

    assert fake_server.created_with == [{"watch_db": None}]


@pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
def test_watch_db_flag_works_with_both_transports(fake_server, transport):
    server_module.main(["--transport", transport, "--watch-db", "data/watch.db"])

    assert fake_server.created_with == [{"watch_db": Path("data/watch.db")}]
    assert fake_server.calls[0][0] == transport


def test_watch_db_flag_needs_a_path(fake_server):
    with pytest.raises(SystemExit):
        server_module.main(["--watch-db"])


# --- 8.4 stdout только для протокола ----------------------------------------------------------


def test_source_has_no_print_calls():
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


def rpc(message_id: int | None, method: str, params: dict | None = None) -> str:
    message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if message_id is not None:
        message["id"] = message_id
    return json.dumps(message) + "\n"


def start_reader(stream) -> queue.Queue:
    lines: queue.Queue = queue.Queue()

    def pump() -> None:
        for line in stream:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return lines


def test_stdout_carries_only_protocol_messages_and_diagnostics_go_to_stderr():
    env = {key: value for key, value in os.environ.items() if "proxy" not in key.lower()}
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_moex.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=ROOT,
    )
    stdout_lines = start_reader(process.stdout)
    stderr_lines = start_reader(process.stderr)
    stdout_seen: list[str] = []

    def wait_for_response(message_id: int) -> dict:
        while True:
            line = stdout_lines.get(timeout=30)
            assert line is not None, "сервер закрыл stdout, не ответив"
            stdout_seen.append(line)
            message = json.loads(line)  # любая строка не в JSON сломала бы протокол
            if message.get("id") == message_id:
                return message

    try:
        process.stdin.write(
            rpc(
                1,
                "initialize",
                {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
            )
        )
        process.stdin.flush()
        assert "result" in wait_for_response(1)

        process.stdin.write(rpc(None, "notifications/initialized"))
        process.stdin.write(rpc(2, "tools/list"))
        process.stdin.flush()
        tools = wait_for_response(2)["result"]["tools"]
        assert {tool["name"] for tool in tools} == {"search_securities", "get_current_price", "get_price_history"}

        # вызов с неверным аргументом: сервер пишет диагностику, в сеть при этом не ходит
        process.stdin.write(rpc(3, "tools/call", {"name": "get_current_price", "arguments": {"secid": "bad ticker"}}))
        process.stdin.flush()
        assert wait_for_response(3)["result"]["isError"] is True

        process.stdin.close()
        process.wait(timeout=30)
        while (line := stdout_lines.get(timeout=5)) is not None:
            stdout_seen.append(line)
    finally:
        process.kill()

    assert stdout_seen, "сервер ничего не ответил"
    for line in stdout_seen:
        assert json.loads(line)["jsonrpc"] == "2.0", f"в stdout попало не сообщение протокола: {line!r}"

    diagnostics = []
    while (line := stderr_lines.get(timeout=5)) is not None:
        diagnostics.append(line)
    stderr = "".join(diagnostics)
    assert "get_current_price" in stderr  # запись о вызове с неверными аргументами
    assert '"jsonrpc"' not in stderr  # протокол в stderr не течёт


def test_watch_mode_over_stdio_keeps_stdout_for_the_protocol_only(tmp_path):
    db_path = tmp_path / "watch" / "watch.db"
    env = {key: value for key, value in os.environ.items() if "proxy" not in key.lower()}
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_moex.server", "--watch-db", str(db_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=ROOT,
    )
    stdout_lines = start_reader(process.stdout)
    stderr_lines = start_reader(process.stderr)
    stdout_seen: list[str] = []

    def wait_for_response(message_id: int) -> dict:
        while True:
            line = stdout_lines.get(timeout=30)
            assert line is not None, "сервер закрыл stdout, не ответив"
            stdout_seen.append(line)
            message = json.loads(line)
            if message.get("id") == message_id:
                return message

    def call(message_id: int, name: str, arguments: dict) -> dict:
        process.stdin.write(rpc(message_id, "tools/call", {"name": name, "arguments": arguments}))
        process.stdin.flush()
        return wait_for_response(message_id)["result"]

    try:
        process.stdin.write(
            rpc(
                1,
                "initialize",
                {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
            )
        )
        process.stdin.flush()
        assert "result" in wait_for_response(1)
        process.stdin.write(rpc(None, "notifications/initialized"))
        process.stdin.write(rpc(2, "tools/list"))
        process.stdin.flush()
        tools = {tool["name"] for tool in wait_for_response(2)["result"]["tools"]}
        assert tools == {
            "search_securities", "get_current_price", "get_price_history",
            "watch_set", "watch_stop", "watch_status", "watch_get_report", "watch_run_due", "watch_ack",
        }  # fmt: skip

        # инструменты расписания без обращения к бирже: в сеть сервер не ходит
        assert call(3, "watch_status", {"chat_id": 1})["structuredContent"] == {"active": False}
        due = call(4, "watch_run_due", {})["structuredContent"]
        assert due["reports"] == [] and due["next_due_at"] is None
        assert call(5, "watch_stop", {"chat_id": 1})["structuredContent"] == {"chat_id": 1, "stopped": False}
        assert call(6, "watch_get_report", {"chat_id": 1})["isError"] is True

        process.stdin.close()
        process.wait(timeout=30)
        while (line := stdout_lines.get(timeout=5)) is not None:
            stdout_seen.append(line)
    finally:
        process.kill()

    for line in stdout_seen:
        assert json.loads(line)["jsonrpc"] == "2.0", f"в stdout попало не сообщение протокола: {line!r}"
    while stderr_lines.get(timeout=5) is not None:
        pass
    assert db_path.exists()  # каталог и файл созданы при первом обращении


def test_watch_package_is_covered_by_the_no_print_check():
    watch_modules = sorted(path.name for path in (PACKAGE / "watch").glob("*.py"))

    assert {"intervals.py", "store.py", "report.py", "service.py"} <= set(watch_modules)
    # test_source_has_no_print_calls обходит PACKAGE.rglob("*.py"), то есть и пакет watch/, и tools/watch.py
    assert (PACKAGE / "tools" / "watch.py").exists()
