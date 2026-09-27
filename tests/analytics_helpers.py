"""Фабрика историй для тестов аналитики: рукописные ряды любого интервала, без сети и фикстур."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from mcp_moex.models import Candle, HistoryResult
from mcp_moex.tools.history import MSK, _summarize

DEFAULT_UNITS = {"stock": "RUB", "fund": "RUB", "bond": "percent_of_face", "index": "points"}


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def make_begins(count: int, interval: str = "day", start: date = date(2026, 1, 5)) -> list[date]:
    if interval == "day":
        return [start + timedelta(days=i) for i in range(count)]
    if interval == "week":
        return [start + timedelta(weeks=i) for i in range(count)]
    if interval == "month":
        first = start.replace(day=1)
        return [_add_months(first, i) for i in range(count)]
    raise ValueError(interval)


def iso(day: date) -> str:
    return datetime(day.year, day.month, day.day, tzinfo=MSK).isoformat()


def make_history(
    secid: str = "SBER",
    closes: Sequence[float] = (100.0, 110.0),
    *,
    opens: Sequence[float] | None = None,
    begins: Sequence[date] | None = None,
    interval: str = "day",
    asset_type: str = "stock",
    price_unit: str | None = None,
    name: str | None = None,
    start: date = date(2026, 1, 5),
    truncated: bool = False,
) -> HistoryResult:
    """История как из `get_price_history`. open первой свечи по умолчанию равен её close, дальше open = прошлый close."""
    days = list(begins) if begins is not None else make_begins(len(closes), interval, start)
    opens = list(opens) if opens is not None else [closes[0], *closes[:-1]]
    has_volume = asset_type != "index"
    candles = [
        Candle(
            begin=iso(day),
            open=o,
            high=max(o, c),
            low=min(o, c),
            close=c,
            volume=1 if has_volume else None,
            turnover=float(c),
        )
        for day, o, c in zip(days, opens, closes, strict=True)
    ]
    return HistoryResult(
        secid=secid,
        name=name or secid,
        asset_type=asset_type,
        price_unit=price_unit or DEFAULT_UNITS[asset_type],
        interval=interval,
        date_from=days[0].isoformat(),
        date_till=days[-1].isoformat(),
        summary=_summarize(candles),
        candles=candles,
        candles_returned=len(candles),
        candles_truncated=truncated,
    )
