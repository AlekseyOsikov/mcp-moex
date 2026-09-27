"""Смоук-клиент: код возврата и указание упавшего шага (на фейковом сервере, без сети)."""

import importlib.util
import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAKE_SERVER = ROOT / "tests" / "fake_mcp_server.py"

spec = importlib.util.spec_from_file_location("smoke_client", ROOT / "scripts" / "smoke_client.py")
smoke_client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke_client)


def run_smoke(capsys, *server_args: str, command: str | None = None) -> tuple[int, str, str]:
    command = command or shlex.join([sys.executable, str(FAKE_SERVER), *server_args])
    code = smoke_client.main(["--server-command", command])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_success_prints_every_step_and_exits_with_zero(capsys):
    code, out, err = run_smoke(capsys)

    assert code == 0, err
    for step in (
        "initialize",
        "list_tools",
        "search_securities",
        "get_current_price",
        "get_price_history",
        "get_price_history (IMOEX)",
        "get_price_history (GAZP)",
        "compute_price_metrics",
        "compare_with_benchmark",
        "compare_securities",
    ):
        assert f"== {step} ==" in out
    assert "OK: все шаги выполнены" in out
    assert '"secid": "SBER"' in out


ALL_TOOLS = [
    "search_securities",
    "get_current_price",
    "get_price_history",
    "compute_price_metrics",
    "compare_with_benchmark",
    "compare_securities",
]


@pytest.mark.parametrize("tool", ALL_TOOLS)
def test_tool_error_fails_the_run_and_names_the_step(capsys, tool):
    code, out, err = run_smoke(capsys, "--fail", tool)

    assert code == 1
    assert f"ОШИБКА на шаге «{tool}»" in err
    assert "имитация сбоя ISS" in err  # причина из текста ошибки инструмента показана
    assert "OK: все шаги выполнены" not in out


@pytest.mark.parametrize("tool", ["get_price_history", "compare_securities"])
def test_missing_tool_fails_the_list_tools_step(capsys, tool):
    code, out, err = run_smoke(capsys, "--without", tool)

    assert code == 1
    assert "ОШИБКА на шаге «list_tools»" in err
    assert tool in err


def test_histories_reach_the_analytics_tools_whole_and_the_right_ones(capsys):
    code, out, err = run_smoke(capsys)

    assert code == 0, err
    # фейк принимает параметры в настоящей модели HistoryResult и возвращает тикеры из полученных историй
    assert '"benchmark_secid": "IMOEX"' in out
    assert '"a_secid": "SBER"' in out and '"b_secid": "GAZP"' in out


def test_unstartable_server_command_fails_and_says_so(capsys):
    code, out, err = run_smoke(capsys, command="/nonexistent/mcp-moex-binary --flag")

    assert code == 1
    assert "ОШИБКА на шаге «запуск сервера»" in err


def test_readme_example_launches_the_server_the_same_way_as_the_smoke_client():
    import re

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    example = re.search(r"## Подключение из проекта агента.*?```python\n(.*?)```", readme, re.S).group(1)
    compile(example, "README.md", "exec")  # пример как минимум синтаксически корректен

    command, *args = smoke_client.default_server_command()
    assert f'command="{command}"' in example
    assert '"run", "--directory", MCP_MOEX_DIR, "mcp-moex"' in example
    assert f'MCP_MOEX_DIR = "{ROOT}"' in example
    assert args == ["run", "--directory", str(ROOT), "mcp-moex"]
    assert f"uv run --directory {ROOT} mcp-moex" in readme
