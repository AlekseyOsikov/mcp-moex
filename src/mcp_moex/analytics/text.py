"""Текст итога, собранный шаблоном без языковой модели, и формат чисел.

Текст строится из уже готового результата и ничего не пересчитывает: числа в нём совпадают с числами структуры
по построению.
"""

from datetime import datetime

from ..models import BenchmarkComparison, Drawdown, MetricsResult, PairComparison


def format_date(iso: str) -> str:
    """`2026-09-01T00:00:00+03:00` -> `01.09.2026`."""
    return datetime.fromisoformat(iso).strftime("%d.%m.%Y")


def format_plain(value: float) -> str:
    """Число с десятичной запятой, до двух знаков, но не меньше одного: `1,5`, `0,0`, `-25,0`."""
    text = f"{abs(value):.2f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return (("-" if value < 0 else "") + text).replace(".", ",")


def format_change(value: float) -> str:
    """Изменение со знаком: `+3,25`, `-1,2`, `0,0`."""
    plain = format_plain(value)
    return f"+{plain}" if value > 0 else plain


PAST_DISCLAIMER = "Прошлое не гарантирует будущий результат."
ADVICE_DISCLAIMER = "Это не индивидуальная инвестиционная рекомендация."
FOOTER = f"{PAST_DISCLAIMER} {ADVICE_DISCLAIMER}"

_INTERVAL_WORDS = {"day": "дневные", "week": "недельные", "month": "месячные", "hour": "часовые"}


def _drawdown(drawdown: Drawdown) -> str:
    if drawdown.peak is None:
        return "просадки не было"
    recovery = f"восстановление {format_date(drawdown.recovered_at)}" if drawdown.recovered_at else "не восстановлена"
    return (
        f"{format_plain(drawdown.depth_pct)}% (пик {format_date(drawdown.peak)}, "
        f"дно {format_date(drawdown.trough)}, {recovery})"
    )


def _period(first: str, last: str, interval: str, count: int, what: str = "свечей") -> str:
    return (
        f"с {format_date(first)} по {format_date(last)} ({_INTERVAL_WORDS[interval]} свечи, {what}: {count}; "
        "даты это начала свечей)"
    )


def _notes_block(notes: list[str]) -> list[str]:
    return ["Замечания:", *(f"- {note}" for note in notes)] if notes else []


def _sensitivity(beta: float | None, correlation: float | None) -> str:
    if beta is None or correlation is None:
        return "Бета и корреляция не рассчитаны."
    return f"Бета: {format_plain(beta)}; корреляция изменений между закрытиями: {format_plain(correlation)}."


def _correlation(correlation: float | None) -> str:
    if correlation is None:
        return "Корреляция не рассчитана."
    return f"Корреляция изменений между закрытиями: {format_plain(correlation)}."


def render_metrics(result: MetricsResult) -> str:
    volatility = (
        f"{format_plain(result.volatility_annualized_pct)}% (при {result.annualization_factor} периодах в году)"
        if result.volatility_annualized_pct is not None
        else "не рассчитана (мало свечей)"
    )
    lines = [
        f"Изменение цены {result.secid} ({result.name}) "
        f"{_period(result.first_candle, result.last_candle, result.interval, result.candles_used)}: "
        f"{format_change(result.change_pct)}%.",
        f"Годовая волатильность: {volatility}.",
        f"Максимальная просадка по закрытиям: {_drawdown(result.max_drawdown)}.",
        f"Текущая просадка от максимального закрытия периода: {format_plain(result.current_drawdown_pct)}%.",
        *_notes_block(result.notes),
        FOOTER,
    ]
    return "\n".join(lines)


def render_benchmark(result: BenchmarkComparison) -> str:
    lines = [
        f"Сравнение изменения цены {result.secid} ({result.name}) с индексом {result.benchmark_secid} "
        f"({result.benchmark_name}) "
        f"{_period(result.first_candle, result.last_candle, result.interval, result.candles_common, 'общих свечей')}.",
        f"Изменение цены {result.secid}: {format_change(result.change_pct)}%; "
        f"изменение индекса {result.benchmark_secid}: {format_change(result.benchmark_change_pct)}%; "
        f"разница: {format_change(result.difference_pp)} п. п. (больше нуля: бумага опередила индекс).",
        _sensitivity(result.beta, result.correlation),
        f"Максимальная просадка {result.secid}: {_drawdown(result.max_drawdown)}.",
        f"Максимальная просадка {result.benchmark_secid}: {_drawdown(result.benchmark_max_drawdown)}.",
        *_notes_block(result.notes),
        FOOTER,
    ]
    return "\n".join(lines)


def render_pair(result: PairComparison) -> str:
    a, b = result.a_secid, result.b_secid
    lines = [
        f"Сравнение изменения цены: A это {a} ({result.a_name}), B это {b} ({result.b_name}) "
        f"{_period(result.first_candle, result.last_candle, result.interval, result.candles_common, 'общих свечей')}.",
        f"Изменение цены A ({a}): {format_change(result.a_change_pct)}%; "
        f"изменение цены B ({b}): {format_change(result.b_change_pct)}%.",
        f"Разница считается как изменение A минус изменение B: {format_change(result.difference_pp)} п. п. "
        "(больше нуля: A опередила B).",
        _correlation(result.correlation),
        f"Максимальная просадка A ({a}): {_drawdown(result.a_max_drawdown)}.",
        f"Максимальная просадка B ({b}): {_drawdown(result.b_max_drawdown)}.",
        *_notes_block(result.notes),
        FOOTER,
    ]
    return "\n".join(lines)
