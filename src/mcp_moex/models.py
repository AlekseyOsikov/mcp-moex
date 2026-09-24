"""Pydantic-модели входов и выходов инструментов.

Описания полей (`description`) попадают в JSON-схемы, которые видит агент, поэтому написаны для LLM.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, model_serializer

AssetType = Literal["stock", "bond", "fund", "index"]
SearchAssetType = Literal["any", "stock", "bond", "fund", "index"]
Interval = Literal["hour", "day", "week", "month"]
PriceSource = Literal["last_trade", "previous_close"]

# --- Входные параметры инструментов -------------------------------------------------------------

QueryParam = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    Field(
        description=(
            "Что искать: название бумаги или эмитента (например, «Сбербанк», «ОФЗ 26238», «золото»), "
            "тикер (SBER) или ISIN (RU0009029540). Точное совпадение с тикером или ISIN идёт первым."
        )
    ),
]

SearchAssetTypeParam = Annotated[
    SearchAssetType,
    Field(
        description=(
            "Класс бумаг: any (все), stock (акции и депозитарные расписки), bond (облигации), "
            "fund (паи биржевых фондов), index (индексы). Указывайте класс, если он известен: "
            "у одного эмитента бывают десятки облигаций, и они вытесняют остальные результаты."
        )
    ),
]

LimitParam = Annotated[
    int,
    Field(ge=1, le=20, description="Максимум результатов, от 1 до 20."),
]

SecidParam = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$"),
    Field(
        description=(
            "Тикер бумаги на Московской бирже, например SBER, SU26238RMFS4, TMOS, IMOEX (регистр не важен). "
            "Если известно только название или ISIN, сначала найдите тикер через search_securities."
        )
    ),
]

DateFromParam = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    Field(description="Начало периода, дата в формате YYYY-MM-DD (включительно).", examples=["2026-01-01"]),
]

DateTillParam = Annotated[
    str | None,
    Field(
        description=(
            "Конец периода, дата в формате YYYY-MM-DD (включительно). "
            "Если не указан, берётся сегодняшняя дата по Москве."
        ),
        examples=["2026-09-10"],
    ),
]

IntervalParam = Annotated[
    Interval,
    Field(description="Размер свечи: hour (час), day (день), week (неделя), month (месяц)."),
]

# --- Результат search_securities ----------------------------------------------------------------


class SecurityHit(BaseModel):
    secid: str = Field(description="Тикер для остальных инструментов (get_current_price, get_price_history).")
    name: str = Field(description="Название бумаги.")
    isin: str | None = Field(description="ISIN или null, если у бумаги его нет (например, у индексов).")
    asset_type: AssetType = Field(description="Класс: stock, bond, fund или index.")
    board: str = Field(description="Основная торговая площадка (например, TQBR).")


class SearchResult(BaseModel):
    query: str = Field(description="Запрос после обрезки пробелов.")
    asset_type: SearchAssetType = Field(description="Класс, по которому шёл поиск.")
    results: list[SecurityHit] = Field(description="Найденные бумаги; точное совпадение тикера или ISIN первым.")
    count: int = Field(description="Число элементов в results.")
    truncated: bool = Field(
        description="true, если найдено больше, чем limit: уточните запрос или сузьте asset_type."
    )


# --- Результат get_current_price ----------------------------------------------------------------

_BOND_ONLY_FIELDS = ("face_value", "currency", "price_rub", "accrued_interest", "dirty_price", "yield_percent")


class PriceResult(BaseModel):
    secid: str = Field(description="Тикер в каноническом написании.")
    name: str = Field(description="Название бумаги.")
    asset_type: AssetType = Field(description="Класс: stock, bond, fund или index.")
    price: float = Field(
        description=(
            "Цена. Единица измерения указана в price_unit: для облигаций это проценты от номинала, "
            "для индексов пункты."
        )
    )
    price_unit: str = Field(
        description=(
            "Единица price: RUB (или код другой валюты) для акций и фондов, percent_of_face для облигаций, "
            "points для индексов."
        )
    )
    price_source: PriceSource = Field(
        description=(
            "last_trade: цена последней сделки (у индекса последнее рассчитанное значение); "
            "previous_close: сегодня сделок не было, взято закрытие предыдущего дня."
        )
    )
    as_of: str = Field(description="Время котировки по данным биржи, ISO 8601, московское время (+03:00).")
    delayed: bool = Field(description="true: данные не реального времени (акции, облигации, фонды; индексы нет).")
    delay_minutes: int = Field(description="Задержка данных в минутах: 15 для акций, облигаций и фондов, 0 для индексов.")
    trading_status: str | None = Field(
        description="Статус торгов по данным биржи (например, T: идут торги); null, если биржа не сообщила."
    )

    # Только для облигаций.
    face_value: float | None = Field(default=None, description="Облигации: номинал.")
    currency: str | None = Field(default=None, description="Облигации: валюта номинала.")
    price_rub: float | None = Field(
        default=None, description="Облигации: цена в валюте номинала (price / 100 * face_value), без НКД."
    )
    accrued_interest: float | None = Field(default=None, description="Облигации: накопленный купонный доход (НКД).")
    dirty_price: float | None = Field(
        default=None, description="Облигации: полная цена с НКД (price_rub + accrued_interest)."
    )
    yield_percent: float | None = Field(
        default=None, description="Облигации: доходность к погашению, % годовых; null, если биржа не сообщила."
    )

    @model_serializer(mode="wrap")
    def _drop_bond_fields_for_other_assets(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.asset_type != "bond":
            for field in _BOND_ONLY_FIELDS:
                data.pop(field, None)
        return data


# --- Результат get_price_history ----------------------------------------------------------------


class Candle(BaseModel):
    begin: str = Field(description="Начало свечи, ISO 8601, московское время (+03:00).")
    open: float
    high: float
    low: float
    close: float
    volume: int | None = Field(description="Объём в штуках; null у индексов.")
    turnover: float | None = Field(description="Оборот в валюте бумаги; null, если биржа не сообщила.")


class HistorySummary(BaseModel):
    candles_count: int = Field(description="Число свечей за весь период (не только возвращённых).")
    start_price: float = Field(description="Цена открытия первой свечи периода.")
    end_price: float = Field(description="Цена закрытия последней свечи периода.")
    min_price: float = Field(description="Наименьшая цена (low) за период.")
    max_price: float = Field(description="Наибольшая цена (high) за период.")
    change_abs: float = Field(description="end_price - start_price.")
    change_pct: float = Field(description="Изменение за период в %: (end_price - start_price) / start_price * 100.")
    first_candle: str = Field(description="Начало первой свечи периода, ISO 8601, +03:00.")
    last_candle: str = Field(description="Начало последней свечи периода, ISO 8601, +03:00.")


class HistoryResult(BaseModel):
    secid: str = Field(description="Тикер в каноническом написании.")
    name: str = Field(description="Название бумаги.")
    asset_type: AssetType = Field(description="Класс: stock, bond, fund или index.")
    price_unit: str = Field(
        description="Единица цен: RUB (или код валюты), percent_of_face (облигации, без пересчёта), points (индексы)."
    )
    interval: Interval = Field(description="Размер свечи.")
    date_from: str = Field(description="Начало периода, YYYY-MM-DD.")
    date_till: str = Field(description="Конец периода, YYYY-MM-DD.")
    summary: HistorySummary | None = Field(
        description="Сводка по всем свечам периода; null, если за период нет данных."
    )
    candles: list[Candle] = Field(description="Свечи по возрастанию времени (не более 300 последних).")
    candles_returned: int = Field(description="Сколько свечей возвращено в candles.")
    candles_truncated: bool = Field(description="true: свечей больше, чем возвращено; сводка учитывает все.")
    message: str | None = Field(default=None, description="Пояснение, например, что за период нет торгов.")
