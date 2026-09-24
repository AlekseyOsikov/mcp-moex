"""Тонкий async-клиент ISS: GET, повторы, кэш в памяти, разбор таблиц `columns`/`data`."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx

from .errors import IssUnavailable

logger = logging.getLogger(__name__)

BASE_URL = "https://iss.moex.com/iss"

Tables = dict[str, list[dict[str, Any]]]

QUOTE_TTL = 10.0
"""Кэш котировок, секунды: данные и так задержаны на 15 минут, кэш защищает от лавины вызовов."""

REFERENCE_TTL = 6 * 60 * 60.0
"""Кэш справочных данных (состав площадок бумаги), секунды."""

_MAX_CACHE_ENTRIES = 512


def parse_tables(payload: Mapping[str, Any]) -> Tables:
    """Превращает ответ ISS вида `{имя: {"columns": [...], "data": [[...]]}}` в списки словарей."""
    tables: Tables = {}
    for name, table in payload.items():
        if isinstance(table, Mapping) and "columns" in table and "data" in table:
            columns = table["columns"]
            tables[name] = [dict(zip(columns, row, strict=False)) for row in table["data"]]
    return tables


class IssClient:
    """Общий async-клиент ISS с повторами на сбоях и кэшем ответов."""

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
        retries: int = 2,
        backoff: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": "mcp-moex/0.1"},
            follow_redirects=True,
            # ISS публичен и доступен напрямую. Прокси из окружения (например, ALL_PROXY=socks://...)
            # httpx не всегда умеет разобрать, и клиент падал бы ещё при создании.
            trust_env=False,
        )
        self._retries = retries
        self._backoff = backoff
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, Tables]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_tables(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        ttl: float = 0.0,
    ) -> Tables:
        """GET `path` (относительно `/iss`, например `securities.json`), таблицы ответа по именам.

        При `ttl > 0` ответ кэшируется в памяти на `ttl` секунд. Возвращаемые данные нельзя менять.
        """
        query = {"iss.meta": "off", **(params or {})}
        key = (path, tuple(sorted((name, str(value)) for name, value in query.items())))

        if ttl > 0:
            cached = self._cache.get(key)
            if cached is not None and cached[0] > self._clock():
                return cached[1]

        tables = await self._fetch(path, query)

        if ttl > 0:
            self._store(key, tables, ttl)
        return tables

    def _store(self, key: tuple[str, tuple[tuple[str, str], ...]], tables: Tables, ttl: float) -> None:
        if len(self._cache) >= _MAX_CACHE_ENTRIES:
            now = self._clock()
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            while len(self._cache) >= _MAX_CACHE_ENTRIES:
                self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (self._clock() + ttl, tables)

    async def _fetch(self, path: str, query: Mapping[str, Any]) -> Tables:
        failure = ""
        for attempt in range(self._retries + 1):
            if attempt:
                logger.warning("ISS %s: повтор %d/%d после сбоя: %s", path, attempt, self._retries, failure)
                await self._sleep(self._backoff * attempt)
            try:
                response = await self._http.get(path, params=query)
            except httpx.TransportError as exc:
                failure = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                continue

            status = response.status_code
            if status == 429 or status >= 500:
                failure = f"HTTP {status}"
                continue
            if not response.is_success:
                raise IssUnavailable(f"HTTP {status} на {path}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise IssUnavailable(f"ответ {path} не является JSON") from exc
            if not isinstance(payload, Mapping):
                raise IssUnavailable(f"неожиданный формат ответа {path}")
            return parse_tables(payload)

        raise IssUnavailable(f"{path}: {failure}")
