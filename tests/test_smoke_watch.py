"""Смоук-клиент режима расписания: сбои дают код 1 и называют шаг (без сети)."""

import importlib.util
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKE_SERVER = ROOT / "tests" / "fake_mcp_server.py"

spec = importlib.util.spec_from_file_location("smoke_watch", ROOT / "scripts" / "smoke_watch.py")
smoke_watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke_watch)


def test_server_without_watch_tools_fails_the_list_tools_step(capsys):
    command = shlex.join([sys.executable, str(FAKE_SERVER)])  # фейковый сервер знает только три инструмента чтения

    code = smoke_watch.main(["--server-command", command])

    err = capsys.readouterr().err
    assert code == 1
    assert "ОШИБКА на шаге «list_tools»" in err
    assert "watch_set" in err


def test_unstartable_server_command_fails_and_says_so(capsys):
    code = smoke_watch.main(["--server-command", "/nonexistent/mcp-moex-binary --flag"])

    assert code == 1
    assert "ОШИБКА на шаге «запуск сервера»" in capsys.readouterr().err


def test_default_command_enables_watch_mode_on_a_temporary_file(monkeypatch):
    seen = {}

    async def fake_run(command):
        seen["command"] = command

    monkeypatch.setattr(smoke_watch, "run", fake_run)

    assert smoke_watch.main([]) == 0
    assert seen["command"][-2] == "--watch-db"
    assert not Path(seen["command"][-1]).exists()  # временный каталог убран
