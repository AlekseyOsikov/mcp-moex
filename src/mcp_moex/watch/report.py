"""Агрегаты по замерам и текст сводки: чистые функции, без SQLite и MCP.

Цены сравниваются в их единицах: для облигаций это проценты от номинала, и изменение в процентах остаётся
корректным относительным изменением. Текст без языковой модели: фиксированный шаблон.
"""

from collections.abc import Sequence
from datetime import datetime

from ..models import Report, TickerAggregate
from ..tools.price import MSK, QUOTE_DELAY_MINUTES
from .store import Sample

BOND_UNIT = "percent_of_face"

DISCLAIMER = (
    f"Котировки акций, облигаций и фондов задержаны на {QUOTE_DELAY_MINUTES} минут, индексы без задержки. "
    "Это не инвестиционная рекомендация."
)


def to_moscow(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, MSK)


def iso_moscow(timestamp: int) -> str:
    """Момент в ISO 8601 со смещением `+03:00`."""
    return to_moscow(timestamp).isoformat()


def aggregate(secid: str, samples: Sequence[Sample]) -> TickerAggregate:
    """Агрегаты одного тикера по замерам периода (в любом порядке; чужие тикеры игнорируются)."""
    own = sorted((s for s in samples if s.secid == secid), key=lambda s: s.observed_at)
    good = [s for s in own if s.ok and s.price is not None]
    failed = len(own) - len(good)
    if not good:
        return TickerAggregate(
            secid=secid, samples=0, failed_samples=failed, first_price=None, last_price=None,
            change_percent=None, min_price=None, max_price=None, price_unit=None, last_quote_at=None,
        )  # fmt: skip
    prices = [s.price for s in good]
    first, last = prices[0], prices[-1]
    quoted = [s.quote_at for s in good if s.quote_at is not None]
    return TickerAggregate(
        secid=secid,
        samples=len(good),
        failed_samples=failed,
        first_price=first,
        last_price=last,
        change_percent=round((last - first) / first * 100, 2) if first else None,
        min_price=min(prices),
        max_price=max(prices),
        price_unit=good[-1].price_unit,
        last_quote_at=iso_moscow(quoted[-1]) if quoted else None,
    )


def build_report(
    chat_id: int,
    secids: Sequence[str],
    samples: Sequence[Sample],
    period_start: int,
    period_end: int,
    report_id: int | None = None,
) -> Report:
    """Сводка за период: агрегат по каждому тикеру опроса (даже без замеров) и текст."""
    tickers = [aggregate(secid, samples) for secid in secids]
    return Report(
        report_id=report_id,
        chat_id=chat_id,
        period_start=iso_moscow(period_start),
        period_end=iso_moscow(period_end),
        text=render_text(tickers, period_start, period_end),
        tickers=tickers,
    )


# --- текст ----------------------------------------------------------------------------------------


def format_number(value: float) -> str:
    """Число как получено, с десятичной запятой: 303.0 -> `303,0`."""
    return repr(float(value)).replace(".", ",")


def format_change(percent: float) -> str:
    """Изменение в процентах: до двух знаков, но не меньше одного: `+0,6`, `-1,25`, `0,0`."""
    text = f"{abs(percent):.2f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    sign = "+" if percent > 0 else "-" if percent < 0 else ""
    return (sign + text).replace(".", ",")


def plural_samples(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        word = "замер"
    elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        word = "замера"
    else:
        word = "замеров"
    return f"{count} {word}"


def _period_title(start: datetime, end: datetime) -> str:
    if start.date() == end.date():
        return f"{start:%d.%m %H:%M} - {end:%H:%M}"
    return f"{start:%d.%m %H:%M} - {end:%d.%m %H:%M}"


def _quote_time(last_quote_at: str, period_end: datetime) -> str:
    quote = datetime.fromisoformat(last_quote_at)
    return f"{quote:%H:%M}" if quote.date() == period_end.date() else f"{quote:%d.%m %H:%M}"


def _ticker_line(ticker: TickerAggregate, period_end: datetime) -> str:
    if ticker.samples == 0:
        detail = f"неудавшихся замеров: {ticker.failed_samples}" if ticker.failed_samples else "замеров не было"
        return f"{ticker.secid}: данных нет ({detail})"

    unit = " % от номинала" if ticker.price_unit == BOND_UNIT else ""
    if ticker.samples == 1:
        parts = [plural_samples(1), f"{format_number(ticker.last_price)}{unit}"]
    else:
        parts = [
            plural_samples(ticker.samples),
            f"{format_number(ticker.first_price)} -> {format_number(ticker.last_price)}{unit}"
            + (f" ({format_change(ticker.change_percent)}%)" if ticker.change_percent is not None else ""),
            f"мин {format_number(ticker.min_price)}",
            f"макс {format_number(ticker.max_price)}",
        ]
    if ticker.last_quote_at:
        parts.append(f"котировка на {_quote_time(ticker.last_quote_at, period_end)}")
    if ticker.failed_samples:
        parts.append(f"неудавшихся замеров: {ticker.failed_samples}")
    return f"{ticker.secid}: " + ", ".join(parts)


def render_text(tickers: Sequence[TickerAggregate], period_start: int, period_end: int) -> str:
    """Текст сводки: период по Москве, строки по тикерам, оговорки о задержке и об отсутствии рекомендации."""
    start, end = to_moscow(period_start), to_moscow(period_end)
    lines = [f"Сводка за {_period_title(start, end)} (МСК)"]
    lines.extend(_ticker_line(ticker, end) for ticker in tickers)
    lines.append(DISCLAIMER)
    return "\n".join(lines)
