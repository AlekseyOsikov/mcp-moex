"""Общее для тестов режима расписания: управляемое время и биржа, отдающая заданные цены."""

import json
from collections.abc import Awaitable, Callable

import httpx

from mcp_moex.iss import IssClient
from mcp_moex.watch.service import WatchService
from mcp_moex.watch.store import WatchStore

from .fake_iss import FixtureTransport

T0 = 1_800_000_000
"""Начальный момент часов теста, секунды Unix."""

_MARKETDATA_PATHS = {
    "SBER": "shares/boards/TQBR/securities/SBER.json",
    "TMOS": "shares/boards/TQBR/securities/TMOS.json",
    "SU26238RMFS4": "bonds/boards/TQOB/securities/SU26238RMFS4.json",
    "IMOEX": "index/boards/SNDX/securities/IMOEX.json",
}
_PRICE_COLUMN = {"SBER": "LAST", "TMOS": "LAST", "SU26238RMFS4": "LAST", "IMOEX": "CURRENTVALUE"}


class Clock:
    """Часы, которыми тест управляет сам: и для сервиса, и для кэша `IssClient`."""

    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ScriptedExchange(httpx.AsyncBaseTransport):
    """Записанные ответы ISS, у которых цена подменена значением из `prices`; `down` имитирует сбой тикера.

    `requests` хранит пути запросов котировок (не карточек бумаг): по ним считаем, сколько раз опросили биржу.
    """

    def __init__(self) -> None:
        self.prices: dict[str, float] = {}
        self.down: set[str] = set()
        self.all_down = False
        self.requests: list[str] = []
        self.on_quote: Callable[[], Awaitable[object]] | None = None
        """Вызывается один раз перед ответом на первый запрос котировки: имитирует событие посреди опроса."""
        self._fixtures = FixtureTransport()

    def quote_requests(self, secid: str) -> int:
        return sum(request.endswith(f"/{secid}.json") for request in self.requests)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self.all_down:
            raise httpx.ConnectTimeout("нет связи", request=request)
        for secid, suffix in _MARKETDATA_PATHS.items():
            if path.endswith(f"/engines/stock/markets/{suffix}"):
                self.requests.append(path)
                if self.on_quote is not None:
                    hook, self.on_quote = self.on_quote, None
                    await hook()
                if secid in self.down:
                    raise httpx.ConnectTimeout("нет связи", request=request)
                response = await self._fixtures.handle_async_request(request)
                if secid in self.prices:
                    payload = json.loads(response.content)
                    columns = payload["marketdata"]["columns"]
                    payload["marketdata"]["data"][0][columns.index(_PRICE_COLUMN[secid])] = self.prices[secid]
                    return httpx.Response(200, json=payload)
                return response
        return await self._fixtures.handle_async_request(request)


class Env:
    """Сервис на временной БД, управляемых часах и подставной бирже."""

    def __init__(self, tmp_path) -> None:
        self.clock = Clock()
        self.exchange = ScriptedExchange()
        self.iss = IssClient(transport=self.exchange, retries=0, clock=self.clock)
        self.db_path = tmp_path / "watch.db"
        self.store = WatchStore(self.db_path)
        self.service = WatchService(self.store, self.iss, self.clock)

    def restart(self) -> WatchService:
        """«Перезапуск»: новый объект хранилища и сервиса на том же файле, новый клиент ISS (кэш пуст)."""
        self.iss = IssClient(transport=self.exchange, retries=0, clock=self.clock)
        self.store = WatchStore(self.db_path)
        self.service = WatchService(self.store, self.iss, self.clock)
        return self.service
