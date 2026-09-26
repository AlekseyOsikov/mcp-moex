"""Сервис расписания: постановка, остановка, состояние, сводки, выполнение наступивших сроков, подтверждение.

Сервер пассивен: не измеряет время сам, все сроки считаются от момента вызова (`clock`). Состояние лежит
в файле SQLite, а не в памяти процесса, потому что клиент запускает сервер на каждое обращение.
"""

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from ..errors import InvalidArguments, MoexError, WatchNotSet
from ..iss import IssClient
from ..models import (
    AckResult,
    FirstSample,
    PolledCounts,
    PriceResult,
    Report,
    RunDueResult,
    WatchSetResult,
    WatchStatusResult,
    WatchStopResult,
)
from ..tools.price import get_current_price
from .intervals import parse_poll_interval, parse_report_interval
from .report import build_report, iso_moscow
from .store import REPORTS_PER_CALL, Claim, Sample, StoredReport, WatchRow, WatchStore

MIN_TICKERS = 1
MAX_TICKERS = 10
MAX_CONCURRENT_QUOTES = 5
"""Одновременных запросов к ISS: 10 тикеров в нескольких чатах не должны превращаться в шквал."""


def _sample_from_price(secid: str, price: PriceResult, now: int) -> Sample:
    return Sample(
        secid=secid,
        observed_at=now,
        ok=True,
        price=price.price,
        price_unit=price.price_unit,
        price_source=price.price_source,
        quote_at=int(datetime.fromisoformat(price.as_of).timestamp()),
    )


def _restore(stored: StoredReport) -> Report:
    return Report.model_validate({**json.loads(stored.payload), "report_id": stored.report_id})


def _build_stored_report(watch: WatchRow, samples: list[Sample], start: int, end: int) -> str:
    return build_report(watch.chat_id, watch.secids, samples, start, end).model_dump_json(exclude={"report_id"})


class WatchService:
    def __init__(
        self,
        store: WatchStore,
        iss: IssClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._iss = iss
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    @staticmethod
    async def _db(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Синхронный вызов SQLite в потоке, чтобы не блокировать цикл событий (в том числе опрос биржи)."""
        return await asyncio.to_thread(function, *args, **kwargs)

    async def _quotes(self, secids: Sequence[str]) -> list[PriceResult | BaseException]:
        """Котировки по тикерам параллельно (не больше 5 запросов сразу); ошибки возвращаются на месте результата."""
        gate = asyncio.Semaphore(MAX_CONCURRENT_QUOTES)

        async def one(secid: str) -> PriceResult:
            async with gate:
                return await get_current_price(self._iss, secid)

        return await asyncio.gather(*(one(secid) for secid in secids), return_exceptions=True)

    # --- постановка, остановка, состояние -------------------------------------------------------

    async def set(self, chat_id: int, secids: Sequence[str], poll_interval: str, report_interval: str) -> WatchSetResult:
        poll = parse_poll_interval(poll_interval)
        report = parse_report_interval(report_interval, poll)
        tickers = list(dict.fromkeys(secid.strip().upper() for secid in secids))
        if not MIN_TICKERS <= len(tickers) <= MAX_TICKERS:
            raise InvalidArguments(
                f"Параметр secids: получено тикеров без повторов: {len(tickers)}, допустимо от {MIN_TICKERS} до "
                f"{MAX_TICKERS}. Оставьте самые важные бумаги и повторите вызов."
            )

        # Первый замер это и проверка тикеров: любая ошибка (неизвестный тикер, нет котировок, ISS недоступна)
        # поднимается до записи, поэтому прежний опрос чата остаётся нетронутым.
        now = self._now()
        quotes = await self._quotes(tickers)
        for outcome in quotes:
            if isinstance(outcome, BaseException):
                raise outcome
        prices: dict[str, PriceResult] = {}
        for price in quotes:
            prices.setdefault(price.secid, price)
        samples = [_sample_from_price(secid, price, now) for secid, price in prices.items()]

        watch, replaced = await self._db(
            self._store.replace_watch,
            chat_id,
            list(prices),
            poll.seconds,
            report.seconds,
            now,
            samples,
            poll_interval=poll.text,
            report_interval=report.text,
        )
        return WatchSetResult(
            chat_id=chat_id,
            secids=list(watch.secids),
            poll_interval=poll.text,
            report_interval=report.text,
            replaced=replaced,
            next_poll_at=iso_moscow(watch.next_poll_at),
            next_report_at=iso_moscow(watch.next_report_at),
            first_samples=[
                FirstSample(secid=secid, price=price.price, price_unit=price.price_unit, quote_at=price.as_of)
                for secid, price in prices.items()
            ],
        )

    async def stop(self, chat_id: int) -> WatchStopResult:
        return WatchStopResult(chat_id=chat_id, stopped=await self._db(self._store.delete_watch, chat_id))

    async def status(self, chat_id: int) -> WatchStatusResult:
        watch = await self._db(self._store.get_watch, chat_id)
        if watch is None:
            return WatchStatusResult(active=False)
        now = self._now()
        samples = await self._db(self._store.count_period_samples, chat_id, watch.window_start, now)
        pending = await self._db(self._store.count_pending, chat_id)
        return WatchStatusResult(
            active=True,
            chat_id=chat_id,
            secids=list(watch.secids),
            poll_interval=watch.poll_interval,
            report_interval=watch.report_interval,
            started_at=iso_moscow(watch.started_at),
            next_poll_at=iso_moscow(watch.next_poll_at),
            next_report_at=iso_moscow(watch.next_report_at),
            samples_in_period=samples,
            pending_reports=pending,
        )

    async def get_report(self, chat_id: int) -> Report:
        """Сводка за текущий период по накопленному: без замеров, без сохранения, без сдвига сроков."""
        watch = await self._db(self._store.get_watch, chat_id)
        if watch is None:
            raise WatchNotSet(chat_id)
        now = self._now()
        samples = await self._db(self._store.period_samples, chat_id, watch.window_start, now)
        return build_report(chat_id, watch.secids, samples, watch.period_start, now)

    # --- выполнение сроков ----------------------------------------------------------------------

    async def run_due(self) -> RunDueResult:
        """Всё, срок чего наступил к моменту вызова: опрос ISS, сводки; плюс неподтверждённые сводки и `next_due_at`."""
        now = self._now()
        claims: list[Claim] = await self._db(self._store.claim_due_polls, now)

        tickers = list(dict.fromkeys(secid for claim in claims for secid in claim.secids))
        outcomes = await self._poll(tickers, now)
        if claims:
            batches = [(claim, [outcomes[secid] for secid in claim.secids]) for claim in claims]
            await self._db(self._store.record_samples, batches)

        await self._db(self._store.create_due_reports, now, _build_stored_report)
        await self._db(self._store.purge, now)
        pending, has_more = await self._db(self._store.pending_reports, REPORTS_PER_CALL)
        next_due = await self._db(self._store.next_due)

        return RunDueResult(
            now=iso_moscow(now),
            next_due_at=iso_moscow(next_due) if next_due is not None else None,
            polled=PolledCounts(
                chats=len(claims),
                tickers=len(tickers),
                failed=sum(not sample.ok for sample in outcomes.values()),
            ),
            reports=[_restore(stored) for stored in pending],
            has_more=has_more,
        )

    async def _poll(self, tickers: Sequence[str], now: int) -> dict[str, Sample]:
        """Замер по каждому уникальному тикеру. Сбой биржи по тикеру это неудавшийся замер; прочие исключения летят дальше."""
        quotes = await self._quotes(tickers)
        outcomes: dict[str, Sample] = {}
        for secid, outcome in zip(tickers, quotes, strict=True):
            if isinstance(outcome, MoexError):
                outcomes[secid] = Sample(secid=secid, observed_at=now, ok=False)
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                outcomes[secid] = _sample_from_price(secid, outcome, now)
        return outcomes

    async def ack(self, report_ids: Sequence[int]) -> AckResult:
        acknowledged = await self._db(self._store.acknowledge, list(report_ids), self._now())
        return AckResult(acknowledged=acknowledged)
