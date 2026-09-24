"""Инструмент get_price_history: свечи за период и сводка, посчитанная сервером."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ..errors import InvalidArguments, RangeTooLarge
from ..iss import QUOTE_TTL, IssClient
from ..models import Candle, HistoryResult, HistorySummary, Interval
from ..resolver import Instrument, resolve

MSK = timezone(timedelta(hours=3))

INTERVAL_CODES: dict[Interval, int] = {"hour": 60, "day": 24, "week": 7, "month": 31}
"""Интервалы свечей ISS: 60 минут, сутки, неделя, месяц."""

PAGE_SIZE = 500
"""ISS отдаёт не больше 500 свечей за запрос."""

MAX_PAGES = 10
"""Не больше 10 страниц (5000 свечей): дальше просим выбрать более крупный интервал."""

MAX_RETURNED_CANDLES = 300
"""Сколько последних свечей попадает в ответ; сводка считается по всем."""

DESCRIPTION = f"""\
История цен бумаги Московской биржи (акции, облигации, фонды, индексы) за период: свечи и готовая сводка.

Возвращает свечи (open, high, low, close, volume, turnover) и summary: цену начала и конца периода, \
минимум, максимум и изменение в процентах, посчитанные по ВСЕМ свечам периода. В списке candles приходят \
не более {MAX_RETURNED_CANDLES} последних свечей (см. candles_truncated). Если свечей слишком много, инструмент \
вернёт ошибку: выберите более крупный interval или сократите период. Время московское (+03:00).

Единицы цен указаны в price_unit: RUB для акций и фондов, percent_of_face для облигаций (проценты \
от номинала, без пересчёта в рубли), points для индексов.

ВАЖНО: цены не скорректированы на сплиты, консолидации и дивидендные гэпы. Если в периоде был сплит \
(цена скачком упала или выросла в разы), изменение за период, минимум и максимум искажены: не делайте \
по ним выводов, не проверив это.

Недельные и месячные свечи попадают в выборку, если их начало приходится на период. \
Если известно только название или ISIN, сначала найдите тикер через search_securities."""


def _parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvalidArguments(f"Параметр {name}: «{value}» не является датой в формате YYYY-MM-DD.") from None


def _iso(begin: str) -> str:
    """`2026-09-01 00:00:00` (московское время) -> `2026-09-01T00:00:00+03:00`."""
    return datetime.strptime(begin, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK).isoformat()


def _price_unit(instrument: Instrument) -> str:
    if instrument.asset_type == "index":
        return "points"
    if instrument.asset_type == "bond":
        return "percent_of_face"
    return instrument.currency


def _to_candle(row: dict[str, Any], *, has_volume: bool) -> Candle:
    volume = row.get("volume")
    turnover = row.get("value")
    return Candle(
        begin=_iso(row["begin"]),
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=int(volume) if has_volume and volume is not None else None,
        turnover=float(turnover) if turnover is not None else None,
    )


def _summarize(candles: list[Candle]) -> HistorySummary:
    start_price = candles[0].open
    end_price = candles[-1].close
    change_abs = float(Decimal(str(end_price)) - Decimal(str(start_price)))
    return HistorySummary(
        candles_count=len(candles),
        start_price=start_price,
        end_price=end_price,
        min_price=min(candle.low for candle in candles),
        max_price=max(candle.high for candle in candles),
        change_abs=change_abs,
        change_pct=round((end_price - start_price) / start_price * 100, 2),
        first_candle=candles[0].begin,
        last_candle=candles[-1].begin,
    )


async def _fetch_candles(
    iss: IssClient, instrument: Instrument, date_from: date, date_till: date, interval: Interval
) -> list[dict[str, Any]]:
    """Все свечи периода: страницы по 500 штук до конца, но не больше `MAX_PAGES` страниц."""
    path = f"{instrument.board_path}/candles.json"
    rows: list[dict[str, Any]] = []
    for page in range(MAX_PAGES + 1):
        tables = await iss.get_tables(
            path,
            {
                "from": date_from.isoformat(),
                "till": date_till.isoformat(),
                "interval": INTERVAL_CODES[interval],
                "start": page * PAGE_SIZE,
            },
            ttl=QUOTE_TTL,
        )
        chunk = tables.get("candles") or []
        if page == MAX_PAGES:
            if chunk:  # после последней разрешённой полной страницы данные ещё есть
                raise RangeTooLarge(interval, MAX_PAGES * PAGE_SIZE)
            break
        rows.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
    return rows


async def get_price_history(
    iss: IssClient,
    secid: str,
    date_from: str,
    date_till: str | None = None,
    interval: Interval = "day",
) -> HistoryResult:
    """Свечи бумаги за период; цены в тех же единицах, что и в `get_current_price`."""
    start = _parse_date(date_from, "date_from")
    end = _parse_date(date_till, "date_till") if date_till else datetime.now(MSK).date()
    if start > end:
        raise InvalidArguments(
            f"Начало периода date_from ({start}) позже конца date_till ({end}). "
            "Поменяйте даты местами или уточните период."
        )

    instrument = await resolve(iss, secid)
    rows = await _fetch_candles(iss, instrument, start, end, interval)

    has_volume = instrument.asset_type != "index"
    candles = sorted((_to_candle(row, has_volume=has_volume) for row in rows), key=lambda candle: candle.begin)
    returned = candles[-MAX_RETURNED_CANDLES:]

    return HistoryResult(
        secid=instrument.secid,
        name=instrument.name,
        asset_type=instrument.asset_type,
        price_unit=_price_unit(instrument),
        interval=interval,
        date_from=start.isoformat(),
        date_till=end.isoformat(),
        summary=_summarize(candles) if candles else None,
        candles=returned,
        candles_returned=len(returned),
        candles_truncated=len(returned) < len(candles),
        message=None
        if candles
        else (
            f"За период с {start} по {end} нет данных: возможно, торгов не было (праздники), "
            "бумага ещё не торговалась или период в будущем."
        ),
    )
