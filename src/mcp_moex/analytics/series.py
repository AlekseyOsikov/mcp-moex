"""Проверка переданной истории и её внутреннее представление.

История приходит от клиента как результат `get_price_history`: верить ей на слово нельзя, а считать по
неполному или повреждённому ряду нельзя тем более. Проверки защищают от порчи данных, но не удостоверяют
их происхождение.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from ..errors import (
    HistoryInconsistent,
    HistoryTruncated,
    IntervalNotSupported,
    NoHistoryData,
)
from ..models import AssetType, HistoryResult, Interval

REL_TOL = 1e-9
ABS_TOL = 1e-9
"""Допуск сверки цен со `summary`: клиент мог перезаписать число, но не его значение."""


@dataclass(frozen=True)
class Bar:
    begin: datetime
    iso: str
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Series:
    """Проверенный ряд свечей одной бумаги."""

    param: str
    secid: str
    name: str
    asset_type: AssetType
    price_unit: str
    interval: Interval
    bars: tuple[Bar, ...]


def _same(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def validate_history(history: HistoryResult, param: str) -> Series:
    """Ряд свечей, если история пригодна для анализа; иначе `MoexError` с названием параметра.

    Порядок проверок фиксирован, чтобы сообщения были предсказуемы: усечена, часовой интервал, нет свечей,
    порядок и повторы, цены, согласованность со сводкой.
    """
    if history.candles_truncated:
        raise HistoryTruncated(param)
    if history.interval == "hour":
        raise IntervalNotSupported(param, history.interval)
    candles = history.candles
    if not candles:
        raise NoHistoryData(param)
    summary = history.summary
    if summary is None:
        raise HistoryInconsistent(param, "есть свечи, но summary равен null")

    bars: list[Bar] = []
    for candle in candles:
        try:
            begin = datetime.fromisoformat(candle.begin)
        except ValueError:
            raise HistoryInconsistent(param, f"begin «{candle.begin}» не является датой ISO 8601") from None
        if begin.tzinfo is None:
            raise HistoryInconsistent(param, f"у begin «{candle.begin}» нет часового пояса")
        if bars and begin <= bars[-1].begin:
            problem = "повторяется" if begin == bars[-1].begin else "идёт не по возрастанию"
            raise HistoryInconsistent(param, f"begin «{candle.begin}» {problem}")
        for label, price in (("open", candle.open), ("close", candle.close)):
            if not math.isfinite(price) or price <= 0:
                raise HistoryInconsistent(
                    param, f"{label} свечи {candle.begin} равен {price}, а должен быть конечным и больше нуля"
                )
        bars.append(Bar(begin, candle.begin, candle.open, candle.high, candle.low, candle.close))

    _check_summary(history, bars, param)
    return Series(
        param=param,
        secid=history.secid,
        name=history.name,
        asset_type=history.asset_type,
        price_unit=history.price_unit,
        interval=history.interval,
        bars=tuple(bars),
    )


def _check_summary(history: HistoryResult, bars: list[Bar], param: str) -> None:
    summary = history.summary
    assert summary is not None
    if summary.candles_count != len(bars):
        raise HistoryInconsistent(param, f"summary.candles_count = {summary.candles_count}, а свечей {len(bars)}")
    checks = (
        ("start_price", summary.start_price, "open первой свечи", bars[0].open),
        ("end_price", summary.end_price, "close последней свечи", bars[-1].close),
        ("min_price", summary.min_price, "наименьший low", min(bar.low for bar in bars)),
        ("max_price", summary.max_price, "наибольший high", max(bar.high for bar in bars)),
    )
    for field, stated, what, actual in checks:
        if not _same(stated, actual):
            raise HistoryInconsistent(param, f"summary.{field} = {stated}, а {what} равен {actual}")
