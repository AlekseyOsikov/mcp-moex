"""Инструмент get_current_price: актуальная цена бумаги в нормализованном виде."""

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from ..errors import MarketDataUnavailable
from ..iss import QUOTE_TTL, IssClient
from ..models import PriceResult, PriceSource
from ..resolver import Instrument, normalize_currency, resolve

MSK = timezone(timedelta(hours=3))
"""Московское время: ISS отдаёт все времена в нём, летнего времени в России нет."""

QUOTE_DELAY_MINUTES = 15
"""Задержка котировок акций, облигаций и фондов; индексы публикуются без задержки."""

DESCRIPTION = f"""\
Актуальная цена бумаги Московской биржи по тикеру: акции, облигации, фонды, индексы. Рынок и площадку \
указывать не нужно, сервер определяет их сам.

Котировки акций, облигаций и фондов ЗАДЕРЖАНЫ на {QUOTE_DELAY_MINUTES} минут (см. delayed, delay_minutes и as_of: \
время котировки по Москве): это не цена «прямо сейчас», говорите об этом пользователю. Значения индексов \
публикуются без задержки. Вне торговой сессии возвращается последняя известная цена; если сегодня сделок не \
было, price_source = previous_close (закрытие предыдущего дня).

Единицы указаны в price_unit: RUB (или код другой валюты) для акций и фондов, points для индексов, \
percent_of_face для облигаций: это ПРОЦЕНТЫ ОТ НОМИНАЛА, а не рубли. Для облигаций в ответе уже есть \
price_rub (цена в валюте номинала, без НКД), accrued_interest (НКД), dirty_price (полная цена с НКД, \
именно её платит покупатель) и yield_percent (доходность к погашению, % годовых).

Если известно только название или ISIN, сначала найдите тикер через search_securities."""


def _positive(value: Any) -> float | None:
    """Число больше нуля или None: ISS отдаёт null (а иногда 0), когда данных ещё нет."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return float(value)


def _parse_systime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK)
    except ValueError:
        return None


def _quote_time(systime: datetime | None, clock: Any) -> datetime | None:
    """Время котировки: дата снимка `SYSTIME` и время `TIME`; в будущем оно быть не может."""
    if systime is None or not clock:
        return None
    try:
        parsed = datetime.strptime(clock, "%H:%M:%S").time()
    except ValueError:
        return None
    quote = datetime.combine(systime.date(), parsed, tzinfo=MSK)
    return quote - timedelta(days=1) if quote > systime else quote


def _pick_price(instrument: Instrument, marketdata: dict[str, Any], security: dict[str, Any]) -> tuple[float, PriceSource]:
    if instrument.asset_type == "index":
        current = _positive(marketdata.get("CURRENTVALUE"))
        if current is not None:
            return current, "last_trade"
        previous = _positive(marketdata.get("LASTVALUE"))
        if previous is not None:
            return previous, "previous_close"
    else:
        last = _positive(marketdata.get("LAST"))
        if last is not None:
            return last, "last_trade"
        previous = _positive(security.get("PREVPRICE"))
        if previous is not None:
            return previous, "previous_close"
    raise MarketDataUnavailable(instrument.secid)


def _as_of(marketdata: dict[str, Any], security: dict[str, Any], source: PriceSource, secid: str) -> str:
    systime = _parse_systime(marketdata.get("SYSTIME"))
    if source == "last_trade":
        quote = _quote_time(systime, marketdata.get("TIME")) or systime
        if quote is not None:
            return quote.isoformat()
    prev_date = security.get("PREVDATE")
    if prev_date:
        return datetime.combine(datetime.fromisoformat(prev_date).date(), time(23, 59, 59), tzinfo=MSK).isoformat()
    if systime is not None:
        return systime.isoformat()
    raise MarketDataUnavailable(secid)


def _bond_fields(price: float, marketdata: dict[str, Any], security: dict[str, Any]) -> dict[str, Any]:
    face = _positive(security.get("FACEVALUE"))
    accrued = security.get("ACCRUEDINT")
    accrued = float(accrued) if isinstance(accrued, int | float) else None

    price_rub = None
    if face is not None:
        # Decimal, чтобы не получать 517.9000000000001 вместо 517.9.
        price_rub = float(Decimal(str(price)) / 100 * Decimal(str(face)))
    dirty_price = None
    if price_rub is not None and accrued is not None:
        dirty_price = float(Decimal(str(price_rub)) + Decimal(str(accrued)))

    yield_value = marketdata.get("YIELD")
    return {
        "face_value": face,
        "currency": normalize_currency(security.get("FACEUNIT") or security.get("CURRENCYID")),
        "price_rub": price_rub,
        "accrued_interest": accrued,
        "dirty_price": dirty_price,
        "yield_percent": float(yield_value) if isinstance(yield_value, int | float) else None,
    }


async def get_current_price(iss: IssClient, secid: str) -> PriceResult:
    """Актуальная цена бумаги по её тикеру; рынок и площадку сервер определяет сам."""
    instrument = await resolve(iss, secid)
    tables = await iss.get_tables(
        f"{instrument.board_path}.json",
        {"iss.only": "marketdata,securities"},
        ttl=QUOTE_TTL,
    )
    marketdata_rows = tables.get("marketdata") or []
    if not marketdata_rows:
        raise MarketDataUnavailable(instrument.secid)
    marketdata = marketdata_rows[0]
    security = (tables.get("securities") or [{}])[0]

    price, source = _pick_price(instrument, marketdata, security)
    is_index = instrument.asset_type == "index"

    fields: dict[str, Any] = {
        "secid": instrument.secid,
        "name": instrument.name,
        "asset_type": instrument.asset_type,
        "price": price,
        "price_source": source,
        "as_of": _as_of(marketdata, security, source, instrument.secid),
        "delayed": not is_index,
        "delay_minutes": 0 if is_index else QUOTE_DELAY_MINUTES,
        "trading_status": marketdata.get("TRADINGSTATUS") or None,
    }
    if is_index:
        fields["price_unit"] = "points"
    elif instrument.asset_type == "bond":
        fields["price_unit"] = "percent_of_face"
        fields.update(_bond_fields(price, marketdata, security))
    else:
        fields["price_unit"] = normalize_currency(security.get("CURRENCYID"))
    return PriceResult(**fields)
