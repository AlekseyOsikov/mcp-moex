"""Статистика по рядам цен: изменения, волатильность, просадка, корреляция и бета. Чистые функции."""

import math
import statistics
from collections.abc import Sequence

from ..models import Drawdown, Interval

ANNUALIZATION_FACTORS: dict[str, int] = {"day": 252, "week": 52, "month": 12}
"""Периодов в году для приведения волатильности; `hour` анализ не поддерживает."""

MIN_CANDLES_FOR_VOLATILITY = 3
MIN_RETURNS_FOR_CORRELATION = 3


def clean(value: float) -> float:
    """Округление до двух знаков без отрицательного нуля."""
    return round(value, 2) + 0.0


def total_change_pct(start_price: float, end_price: float) -> float:
    """Изменение за период тем же выражением, что в `get_price_history`, чтобы совпадать со `summary.change_pct`."""
    return clean((end_price - start_price) / start_price * 100)


def period_ratios(first_open: float, closes: Sequence[float]) -> list[float]:
    """Множитель цены за каждую свечу: первая от `open`, остальные от `close` предыдущей."""
    bases = [first_open, *closes[:-1]]
    return [close / base for close, base in zip(closes, bases, strict=True)]


def ratio_to_pct(ratio: float) -> float:
    return clean((ratio - 1) * 100)


def close_returns(closes: Sequence[float]) -> list[float]:
    """Простые изменения между соседними закрытиями (долей, не процентах)."""
    return [current / previous - 1 for previous, current in zip(closes, closes[1:])]


def annualization_factor(interval: Interval) -> int:
    return ANNUALIZATION_FACTORS[interval]


def volatility_annualized_pct(closes: Sequence[float], interval: Interval) -> float | None:
    """Выборочное стандартное отклонение изменений между закрытиями, приведённое к году; `null` при менее чем трёх свечах."""
    if len(closes) < MIN_CANDLES_FOR_VOLATILITY:
        return None
    deviation = statistics.stdev(close_returns(closes))
    return clean(deviation * math.sqrt(annualization_factor(interval)) * 100)


def max_drawdown(closes: Sequence[float], begins: Sequence[str]) -> Drawdown:
    """Максимальная просадка по закрытиям.

    Пик обновляется только строго большим значением (при равных максимумах пик самый ранний). Восстановление:
    первая свеча после дна с закрытием не ниже пика. Без просадки глубина 0, а пик, дно и восстановление `null`.
    """
    peak_index = 0
    worst = 0.0
    worst_peak = worst_trough = -1
    for index, close in enumerate(closes):
        if close > closes[peak_index]:
            peak_index = index
        depth = close / closes[peak_index] - 1
        if depth < worst:
            worst, worst_peak, worst_trough = depth, peak_index, index
    if worst_trough < 0:
        return Drawdown(depth_pct=0.0, peak=None, trough=None, recovered_at=None)

    recovered = next(
        (index for index in range(worst_trough + 1, len(closes)) if closes[index] >= closes[worst_peak]), None
    )
    return Drawdown(
        depth_pct=clean(worst * 100),
        peak=begins[worst_peak],
        trough=begins[worst_trough],
        recovered_at=begins[recovered] if recovered is not None else None,
    )


def current_drawdown_pct(closes: Sequence[float]) -> float:
    """От максимального закрытия периода до последнего закрытия, не больше нуля."""
    return clean((closes[-1] / max(closes) - 1) * 100)


def beta_and_correlation(
    returns: Sequence[float], benchmark_returns: Sequence[float]
) -> tuple[float | None, float | None]:
    """Бета (ковариация к дисперсии второго ряда) и корреляция по изменениям между закрытиями.

    Оба `None`, если изменений меньше трёх или разброс любого из рядов нулевой. Корреляция ограничена
    отрезком [-1; 1] до округления, чтобы шум float не дал 1.0000000000000002.
    """
    if len(returns) < MIN_RETURNS_FOR_CORRELATION or len(returns) != len(benchmark_returns):
        return None, None
    variance = statistics.variance(returns)
    benchmark_variance = statistics.variance(benchmark_returns)
    if variance == 0 or benchmark_variance == 0:
        return None, None
    covariance = statistics.covariance(returns, benchmark_returns)
    correlation = covariance / math.sqrt(variance * benchmark_variance)
    return clean(covariance / benchmark_variance), clean(max(-1.0, min(1.0, correlation)))
