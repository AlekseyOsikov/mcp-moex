"""Транспорт httpx для тестов: отдаёт записанные ответы ISS из `tests/fixtures/`, без сети.

Запись новых фикстур: `RECORD_FIXTURES=1 uv run pytest` (недостающие ответы берутся с живого ISS)
или `uv run python -m tests.record_fixtures` (заранее заданный список запросов).
"""

import hashlib
import json
import os
from pathlib import Path

import httpx

FIXTURES_DIR = Path(__file__).parent / "fixtures"
INDEX_FILE = FIXTURES_DIR / "_index.json"
RECORD_ENV = "RECORD_FIXTURES"


class MissingFixture(Exception):
    """Тесту понадобился ответ ISS, которого нет среди записанных."""


def fixture_path(url: httpx.URL) -> Path:
    """Имя файла: путь запроса плюс хеш отсортированных параметров (без `iss.meta`)."""
    name = url.path.removeprefix("/iss/").removesuffix(".json").replace("/", "__")
    params = sorted((key, value) for key, value in url.params.multi_items() if key != "iss.meta")
    if params:
        query = "&".join(f"{key}={value}" for key, value in params)
        name += "__" + hashlib.sha1(query.encode()).hexdigest()[:10]
    return FIXTURES_DIR / f"{name}.json"


def _readable(url: httpx.URL) -> str:
    params = [(key, value) for key, value in url.params.multi_items() if key != "iss.meta"]
    query = "&".join(f"{key}={value}" for key, value in params)
    return url.path.removeprefix("/iss/") + (f"?{query}" if query else "")


class FixtureTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = fixture_path(request.url)
        if not path.exists():
            if os.environ.get(RECORD_ENV) != "1":
                raise MissingFixture(
                    f"Нет фикстуры {path.name} для запроса {_readable(request.url)}. "
                    f"Запишите её: {RECORD_ENV}=1 uv run pytest"
                )
            await _record(request, path)
        return httpx.Response(200, content=path.read_bytes(), headers={"content-type": "application/json"})


class CountingTransport(httpx.AsyncBaseTransport):
    """Считает запросы, прошедшие к записанным ответам (по пути запроса)."""

    def __init__(self) -> None:
        self._inner = FixtureTransport()
        self.paths: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        return await self._inner.handle_async_request(request)

    def count(self, fragment: str) -> int:
        return sum(fragment in path for path in self.paths)


class MutatingTransport(httpx.AsyncBaseTransport):
    """Отдаёт записанный ответ, предварительно изменив его: `mutate(путь_запроса, payload)`."""

    def __init__(self, mutate) -> None:
        self._inner = FixtureTransport()
        self._mutate = mutate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        payload = json.loads(response.content)
        self._mutate(request.url.path, payload)
        return httpx.Response(200, json=payload)


def set_column(payload: dict, table: str, column: str, value, row: int = 0) -> None:
    """Меняет значение колонки в строке таблицы ответа ISS."""
    index = payload[table]["columns"].index(column)
    payload[table]["data"][row][index] = value


async def _record(request: httpx.Request, path: Path) -> None:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, trust_env=False) as client:
        response = await client.get(str(request.url))
    response.raise_for_status()
    FIXTURES_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(response.json(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    index = json.loads(INDEX_FILE.read_text(encoding="utf-8")) if INDEX_FILE.exists() else {}
    index[path.name] = _readable(request.url)
    INDEX_FILE.write_text(
        json.dumps(dict(sorted(index.items())), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
