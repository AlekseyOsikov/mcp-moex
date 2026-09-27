"""Общее ядро аналитики: ряд за окно, выравнивание двух рядов по датам, признак незавершённой свечи, замечания."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..errors import TooFewCommonCandles
from ..models import Drawdown, Interval
from . import stats
from .series import Bar, Series
from .text import format_date

SPLIT_RATIO = 1.8
"""Закрытие изменилось между соседними свечами в столько раз и более (в любую сторону): вероятен сплит."""

SMALL_SAMPLE_RETURNS = 10
"""Меньше стольких изменений между закрытиями оценки волатильности и корреляции малонадёжны."""

MAX_SPLIT_DATES = 3

DIVIDENDS_NOTE = "Изменение цены не учитывает дивиденды и купоны и не равно доходности инвестора."
BOND_NOTE = (
    "Цена облигации выражена в процентах от номинала без накопленного купонного дохода: "
    "изменение цены не равно доходу по облигации."
)
BENCHMARK_NOTE = (
    "Бумага считается по цене без дивидендов, поэтому корректно сравнивать её с ценовым индексом (например, IMOEX); "
    "индекс полной доходности (например, MCFTR) включает дивиденды и покажет бумагу заметно хуже, чем она есть."
)
CURRENCY_NOTE = (
    "У бумаг разные валюты котировки ({a_unit} и {b_unit}): изменения выражены в разных валютах, "
    "эффект курса для них не одинаков."
)


# --- ряд за окно ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Leg:
    """Один ряд в расчётном окне: все свечи (метрики) или только общие с другим рядом (сравнения)."""

    series: Series
    bars: tuple[Bar, ...]
    closes: list[float]
    begins: list[str]
    ratios: list[float]
    period_pct: list[float]
    change_pct: float
    drawdown: Drawdown
    returns: list[float]


def make_leg(series: Series, bars: Sequence[Bar] | None = None) -> Leg:
    window = tuple(bars if bars is not None else series.bars)
    closes = [bar.close for bar in window]
    ratios = stats.period_ratios(window[0].open, closes)
    begins = [bar.iso for bar in window]
    return Leg(
        series=series,
        bars=window,
        closes=closes,
        begins=begins,
        ratios=ratios,
        period_pct=[stats.ratio_to_pct(ratio) for ratio in ratios],
        change_pct=stats.total_change_pct(window[0].open, closes[-1]),
        drawdown=stats.max_drawdown(closes, begins),
        returns=stats.close_returns(closes),
    )


@dataclass(frozen=True)
class Aligned:
    a: Leg
    b: Leg

    @property
    def begins(self) -> list[str]:
        return self.a.begins

    @property
    def common(self) -> int:
        return len(self.a.bars)


def align(a: Series, b: Series) -> Aligned:
    """Общие свечи двух рядов по `begin`; меньше двух общих это ошибка."""
    b_begins = {bar.begin for bar in b.bars}
    common = {bar.begin for bar in a.bars} & b_begins
    if len(common) < 2:
        raise TooFewCommonCandles(len(common))
    return Aligned(
        make_leg(a, [bar for bar in a.bars if bar.begin in common]),
        make_leg(b, [bar for bar in b.bars if bar.begin in common]),
    )


# --- незавершённая свеча -------------------------------------------------------------------------


def period_last_day(begin: datetime, interval: Interval) -> date:
    """Последний календарный день периода свечи: день, неделя (begin + 6 дней) или месяц."""
    day = begin.date()
    if interval == "week":
        return day + timedelta(days=6)
    if interval == "month":
        return (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return day


def is_partial(bar: Bar, interval: Interval, today: date) -> bool:
    """Период ещё не завершён на сегодняшнюю дату по Москве (сравнение только по дате: безопасно в сторону «не завершён»)."""
    return today <= period_last_day(bar.begin, interval)


def partial_flags(leg: Leg, today: date) -> list[bool]:
    return [is_partial(bar, leg.series.interval, today) for bar in leg.bars]


# --- замечания -----------------------------------------------------------------------------------


def split_suspects(leg: Leg) -> list[str]:
    """`begin` свечей, закрытие которых отличается от предыдущего в `SPLIT_RATIO` раз и более в любую сторону."""
    return [
        leg.begins[index]
        for index in range(1, len(leg.closes))
        if not 1 / SPLIT_RATIO < leg.closes[index] / leg.closes[index - 1] < SPLIT_RATIO
    ]


def split_note(leg: Leg) -> str | None:
    suspects = split_suspects(leg)
    if not suspects:
        return None
    dates = ", ".join(format_date(begin) for begin in suspects[:MAX_SPLIT_DATES])
    more = f" и ещё {len(suspects) - MAX_SPLIT_DATES}" if len(suspects) > MAX_SPLIT_DATES else ""
    return (
        f"Возможен сплит или консолидация акций у {leg.series.secid}: закрытие изменилось не менее чем в "
        f"{str(SPLIT_RATIO).replace('.', ',')} раза между соседними свечами ({dates}{more}). "
        "Цены не скорректированы, метрики могут быть искажены."
    )


def partial_note(leg: Leg, today: date) -> str | None:
    if not partial_flags(leg, today)[-1]:
        return None
    return (
        f"Последний период (свеча от {format_date(leg.begins[-1])}) ещё не завершён: "
        "его изменение может измениться."
    )


def _compact(notes: Sequence[str | None]) -> list[str]:
    return [note for note in notes if note]


def metrics_notes(leg: Leg, volatility: float | None, today: date) -> list[str]:
    """Замечания к метрикам одной бумаги."""
    if volatility is None:
        sample = "Выборка мала: свечей меньше трёх, волатильность не рассчитана."
    elif len(leg.returns) < SMALL_SAMPLE_RETURNS:
        sample = "Выборка мала: изменений между закрытиями меньше десяти, волатильность малонадёжна."
    else:
        sample = None
    return _compact(
        [
            DIVIDENDS_NOTE,
            BOND_NOTE if leg.series.asset_type == "bond" else None,
            partial_note(leg, today),
            split_note(leg),
            sample,
        ]
    )


def comparison_notes(
    aligned: Aligned, correlation: float | None, today: date, *, benchmark: bool
) -> list[str]:
    """Замечания к сравнению: общие правила плюс тип эталона или разные валюты котировки."""
    a, b = aligned.a, aligned.b
    if benchmark:
        missing, unreliable = "корреляция и бета не рассчитаны", "корреляция и бета малонадёжны"
    else:
        missing, unreliable = "корреляция не рассчитана", "корреляция малонадёжна"
    if len(a.returns) < stats.MIN_RETURNS_FOR_CORRELATION:
        sample = f"Выборка мала: общих свечей меньше четырёх, {missing}."
    elif correlation is None:
        sample = f"Разброс изменений одной из бумаг нулевой: {missing}."
    elif len(a.returns) < SMALL_SAMPLE_RETURNS:
        sample = f"Выборка мала: изменений между закрытиями меньше десяти, {unreliable}."
    else:
        sample = None

    first, last = aligned.a.bars[0].begin, aligned.a.bars[-1].begin
    shorter = [
        leg.series.secid for leg in (a, b) if leg.series.bars[0].begin < first or leg.series.bars[-1].begin > last
    ]
    window = (
        f"Окно расчёта с {format_date(aligned.begins[0])} по {format_date(aligned.begins[-1])} "
        f"(общих свечей: {aligned.common}) короче периода истории {' и '.join(shorter)}: "
        "изменения посчитаны только по общим датам."
        if shorter
        else None
    )
    skipped = [
        (leg.series.secid, sum(first <= bar.begin <= last for bar in leg.series.bars) - aligned.common)
        for leg in (a, b)
    ]
    gaps = [f"{secid} ({count})" for secid, count in skipped if count > 0]
    skipped_note = (
        "Внутри окна есть свечи, которых нет у второй бумаги (например, торги в выходные): они не учтены, "
        f"изменения посчитаны по общим датам. Пропущено свечей: {', '.join(gaps)}."
        if gaps
        else None
    )
    currency = (
        CURRENCY_NOTE.format(a_unit=a.series.price_unit, b_unit=b.series.price_unit)
        if not benchmark and a.series.price_unit != b.series.price_unit
        else None
    )
    return _compact(
        [
            DIVIDENDS_NOTE,
            BENCHMARK_NOTE if benchmark else None,
            currency,
            partial_note(a, today),
            split_note(a),
            split_note(b),
            window,
            skipped_note,
            sample,
        ]
    )
