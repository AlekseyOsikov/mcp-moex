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


# --- Режим расписания (moex-price-watch) --------------------------------------------------------
# Инструменты служебные: их вызывает клиент-планировщик, а не модель.

ChatIdParam = Annotated[
    int,
    Field(description="Идентификатор чата (целое число), в котором ведётся опрос. Задаёт клиент-планировщик."),
]

WatchSecidsParam = Annotated[
    list[SecidParam],
    Field(
        description=(
            "Тикеры бумаг для опроса, от 1 до 10 (регистр не важен, повторы убираются). "
            "Если известно только название или ISIN, сначала найдите тикер через search_securities."
        )
    ),
]

PollIntervalParam = Annotated[
    str,
    Field(
        description=(
            "Как часто опрашивать цены: целое число и единица m (минуты), h (часы) или d (сутки), "
            "например 15m, 1h, 1d. От 5m до 1d. Время суток не поддерживается."
        ),
        examples=["15m"],
    ),
]

ReportIntervalParam = Annotated[
    str,
    Field(
        description=(
            "Как часто формировать сводку: тот же формат, что у poll_interval (15m, 1h, 1d). "
            "Не меньше poll_interval и не больше 7d."
        ),
        examples=["1h"],
    ),
]

ReportIdsParam = Annotated[
    list[int],
    Field(description="Идентификаторы сводок (report_id из watch_run_due), которые успешно доставлены."),
]


class FirstSample(BaseModel):
    secid: str = Field(description="Тикер в каноническом написании.")
    price: float = Field(description="Цена первого замера в единицах price_unit.")
    price_unit: str = Field(description="Единица цены: RUB (или код валюты), percent_of_face, points.")
    quote_at: str = Field(description="Время котировки по данным биржи, ISO 8601, +03:00.")


class WatchSetResult(BaseModel):
    chat_id: int = Field(description="Идентификатор чата.")
    secids: list[str] = Field(description="Канонические тикеры опроса без повторов, в порядке ввода.")
    poll_interval: str = Field(description="Период опроса в принятом виде (без пробелов, строчными).")
    report_interval: str = Field(description="Период сводки в принятом виде.")
    replaced: bool = Field(description="true: для чата уже был опрос, он заменён новым.")
    next_poll_at: str = Field(description="Момент следующего опроса, ISO 8601, +03:00.")
    next_report_at: str = Field(description="Момент следующей сводки, ISO 8601, +03:00.")
    first_samples: list[FirstSample] = Field(description="Первые замеры, сделанные при постановке, по тикерам.")


class WatchStopResult(BaseModel):
    chat_id: int = Field(description="Идентификатор чата.")
    stopped: bool = Field(description="true: опрос был и остановлен; false: опроса не было.")


class WatchStatusResult(BaseModel):
    active: bool = Field(description="true: для чата задан опрос. Если false, остальных полей нет.")
    chat_id: int | None = Field(default=None, description="Идентификатор чата.")
    secids: list[str] | None = Field(default=None, description="Тикеры опроса.")
    poll_interval: str | None = Field(default=None, description="Период опроса.")
    report_interval: str | None = Field(default=None, description="Период сводки.")
    started_at: str | None = Field(default=None, description="Когда задан опрос, ISO 8601, +03:00.")
    next_poll_at: str | None = Field(default=None, description="Следующий опрос, ISO 8601, +03:00.")
    next_report_at: str | None = Field(default=None, description="Следующая сводка, ISO 8601, +03:00.")
    samples_in_period: int | None = Field(default=None, description="Замеров в текущем периоде сводки.")
    pending_reports: int | None = Field(default=None, description="Сводок чата, ещё не подтверждённых через watch_ack.")

    @model_serializer(mode="wrap")
    def _only_active_flag_when_inactive(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        return data if self.active else {"active": False}


class TickerAggregate(BaseModel):
    secid: str = Field(description="Тикер.")
    samples: int = Field(description="Число удавшихся замеров за период.")
    failed_samples: int = Field(description="Число неудавшихся замеров (биржа не отдала котировку).")
    first_price: float | None = Field(description="Цена первого замера периода; null, если замеров нет.")
    last_price: float | None = Field(description="Цена последнего замера периода; null, если замеров нет.")
    change_percent: float | None = Field(description="Изменение last_price к first_price в %, 2 знака; null без замеров.")
    min_price: float | None = Field(description="Наименьшая цена замеров периода; null без замеров.")
    max_price: float | None = Field(description="Наибольшая цена замеров периода; null без замеров.")
    price_unit: str | None = Field(description="Единица цены; null без замеров.")
    last_quote_at: str | None = Field(description="Время последней котировки по данным биржи, ISO 8601, +03:00.")


class Report(BaseModel):
    report_id: int | None = Field(description="Идентификатор для watch_ack; null у сводки по запросу (watch_get_report).")
    chat_id: int = Field(description="Идентификатор чата.")
    period_start: str = Field(description="Начало периода сводки, ISO 8601, +03:00.")
    period_end: str = Field(description="Конец периода сводки, ISO 8601, +03:00.")
    text: str = Field(description="Готовый русский текст сводки: отправляйте пользователю как есть.")
    tickers: list[TickerAggregate] = Field(description="Агрегаты по тикерам опроса.")


class PolledCounts(BaseModel):
    chats: int = Field(description="Сколько чатов опрошено за вызов.")
    tickers: int = Field(description="Сколько уникальных тикеров опрошено за вызов.")
    failed: int = Field(description="Сколько из них не удалось опросить.")


class RunDueResult(BaseModel):
    now: str = Field(description="Момент вызова, ISO 8601, +03:00.")
    next_due_at: str | None = Field(
        description="Ближайший будущий срок опроса или сводки среди всех чатов; null, если опросов нет."
    )
    polled: PolledCounts = Field(description="Что опрошено за этот вызов.")
    reports: list[Report] = Field(description="Сводки к отправке (не подтверждённые через watch_ack), старые первыми.")
    has_more: bool = Field(description="true: неподтверждённых сводок больше 100; вызовите watch_run_due ещё раз.")


class AckResult(BaseModel):
    acknowledged: int = Field(description="Сколько сводок отмечено доставленными (повторные и неизвестные не считаются).")
