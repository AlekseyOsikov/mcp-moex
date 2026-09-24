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

    def run(self, transport: str, **kwargs) -> None:
        self.calls.append((transport, kwargs))


@pytest.fixture
def fake_server(monkeypatch) -> FakeServer:
    fake = FakeServer()
    monkeypatch.setattr(server_module, "create_server", lambda: fake)
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
