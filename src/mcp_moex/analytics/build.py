"""Сборка результатов трёх инструментов из проверенных рядов.

Порядок проверок между историями: роли (эталон это индекс; в сравнении двух бумаг индекса нет), облигации,
совпадение `interval`, число общих свечей. Числа `text` берутся из готового результата.
"""

from datetime import date

from ..errors import (
    BenchmarkMustBeIndex,
    BondNotComparable,
    IndexNotAllowed,
    IntervalMismatch,
)
from ..models import (
    BenchmarkComparison,
    BenchmarkPeriod,
    MetricsResult,
    PairComparison,
    PairPeriod,
    PeriodChange,
)
from . import core, stats, text
from .series import Series


def metrics(series: Series, today: date) -> MetricsResult:
    leg = core.make_leg(series)
    volatility = stats.volatility_annualized_pct(leg.closes, series.interval)
    result = MetricsResult(
        secid=series.secid,
        name=series.name,
        asset_type=series.asset_type,
        price_unit=series.price_unit,
        interval=series.interval,
        first_candle=leg.begins[0],
        last_candle=leg.begins[-1],
        candles_used=len(leg.bars),
        change_pct=leg.change_pct,
        volatility_annualized_pct=volatility,
        annualization_factor=stats.annualization_factor(series.interval),
        max_drawdown=leg.drawdown,
        current_drawdown_pct=stats.current_drawdown_pct(leg.closes),
        periods=[
            PeriodChange(begin=begin, change_pct=pct, partial=partial)
            for begin, pct, partial in zip(leg.begins, leg.period_pct, core.partial_flags(leg, today), strict=True)
        ],
        notes=core.metrics_notes(leg, volatility, today),
        text="",
    )
    return result.model_copy(update={"text": text.render_metrics(result)})


def _check_common(a: Series, b: Series) -> None:
    if a.interval != b.interval:
        raise IntervalMismatch(a.param, a.interval, b.param, b.interval)


def benchmark(series: Series, benchmark_series: Series, today: date) -> BenchmarkComparison:
    if benchmark_series.asset_type != "index":
        raise BenchmarkMustBeIndex(benchmark_series.param, benchmark_series.secid, benchmark_series.asset_type)
    if series.asset_type == "bond":
        raise BondNotComparable(series.param, series.secid)
    _check_common(series, benchmark_series)

    aligned = core.align(series, benchmark_series)
    beta, correlation = stats.beta_and_correlation(aligned.a.returns, aligned.b.returns)
    result = BenchmarkComparison(
        secid=series.secid,
        name=series.name,
        asset_type=series.asset_type,
        price_unit=series.price_unit,
        benchmark_secid=benchmark_series.secid,
        benchmark_name=benchmark_series.name,
        interval=series.interval,
        first_candle=aligned.begins[0],
        last_candle=aligned.begins[-1],
        candles_common=aligned.common,
        change_pct=aligned.a.change_pct,
        benchmark_change_pct=aligned.b.change_pct,
        difference_pp=stats.clean(aligned.a.change_pct - aligned.b.change_pct),
        beta=beta,
        correlation=correlation,
        max_drawdown=aligned.a.drawdown,
        benchmark_max_drawdown=aligned.b.drawdown,
        periods=[
            BenchmarkPeriod(
                begin=begin,
                change_pct=own,
                benchmark_change_pct=other,
                difference_pp=stats.clean(own - other),
                partial=partial,
            )
            for begin, own, other, partial in zip(
                aligned.begins, aligned.a.period_pct, aligned.b.period_pct, core.partial_flags(aligned.a, today), strict=True
            )
        ],
        notes=core.comparison_notes(aligned, correlation, today, benchmark=True),
        text="",
    )
    return result.model_copy(update={"text": text.render_benchmark(result)})


def pair(a: Series, b: Series, today: date) -> PairComparison:
    for series in (a, b):
        if series.asset_type == "index":
            raise IndexNotAllowed(series.param, series.secid)
    for series in (a, b):
        if series.asset_type == "bond":
            raise BondNotComparable(series.param, series.secid)
    _check_common(a, b)

    aligned = core.align(a, b)
    _, correlation = stats.beta_and_correlation(aligned.a.returns, aligned.b.returns)
    result = PairComparison(
        a_secid=a.secid,
        a_name=a.name,
        a_price_unit=a.price_unit,
        b_secid=b.secid,
        b_name=b.name,
        b_price_unit=b.price_unit,
        interval=a.interval,
        first_candle=aligned.begins[0],
        last_candle=aligned.begins[-1],
        candles_common=aligned.common,
        a_change_pct=aligned.a.change_pct,
        b_change_pct=aligned.b.change_pct,
        difference_pp=stats.clean(aligned.a.change_pct - aligned.b.change_pct),
        correlation=correlation,
        a_max_drawdown=aligned.a.drawdown,
        b_max_drawdown=aligned.b.drawdown,
        periods=[
            PairPeriod(
                begin=begin,
                a_change_pct=pct_a,
                b_change_pct=pct_b,
                difference_pp=stats.clean(pct_a - pct_b),
                partial=partial,
            )
            for begin, pct_a, pct_b, partial in zip(
                aligned.begins, aligned.a.period_pct, aligned.b.period_pct, core.partial_flags(aligned.a, today), strict=True
            )
        ],
        notes=core.comparison_notes(aligned, correlation, today, benchmark=False),
        text="",
    )
    return result.model_copy(update={"text": text.render_pair(result)})
