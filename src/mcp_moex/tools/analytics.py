"""Инструменты анализа истории: метрики одной бумаги и два вида сравнения. Без сети и без состояния."""

from datetime import date

from ..analytics import build
from ..analytics.series import validate_history
from ..models import BenchmarkComparison, HistoryResult, MetricsResult, PairComparison

_INPUT_RULES = """\
Параметры принимают результат get_price_history целиком, без изменений (свечи и summary из одного вызова): \
не пересчитывайте, не сокращайте и не правьте их. Историю бумаги сначала получите через get_price_history, \
а если известно только название или ISIN, тикер найдите через search_securities. Для длинных периодов \
берите interval = "week" или "month": усечённая история (свечей больше 300) и часовые свечи не принимаются."""

_CAVEATS = """\
Считается изменение цены, а не доходность инвестора: дивиденды, купоны и сплиты не учитываются (цены не \
скорректированы, при подозрении на сплит в notes будет предупреждение). Все числа посчитаны сервером; в \
notes лежат предупреждения, в text готовый русский текст: передавайте их пользователю вместе с числами."""

METRICS_DESCRIPTION = f"""\
Метрики одной бумаги за период по её истории: изменение цены, годовая волатильность, максимальная и текущая \
просадка и изменение по каждой свече (по месяцам, неделям или дням).

{_INPUT_RULES}

Подходит акциям, фондам, индексам и облигациям (для облигации цена в процентах от номинала без накопленного \
купонного дохода, изменение цены не равно доходу). Для сравнения бумаги с индексом используйте \
compare_with_benchmark, для сравнения двух бумаг compare_securities.

{_CAVEATS}"""

BENCHMARK_DESCRIPTION = f"""\
Сравнение бумаги с эталонным индексом по общим датам: изменение цены бумаги и индекса, разница в процентных \
пунктах (обгоняла ли бумага рынок), бета, корреляция, максимальные просадки и таблица по периодам. Это \
инструмент для сравнения с индексом (например, IMOEX); две бумаги между собой сравнивает compare_securities.

В history передайте историю бумаги, в benchmark_history историю индекса (asset_type = index): получите обе \
через get_price_history с одним и тем же interval и одним периодом и передайте без изменений. Облигации не \
сравниваются.

{_INPUT_RULES}

{_CAVEATS}"""

SECURITIES_DESCRIPTION = f"""\
Сравнение двух бумаг между собой по общим датам: изменение цены каждой, разница A минус B в процентных \
пунктах, корреляция, максимальные просадки и таблица по периодам. Беты нет. Только для двух бумаг (акции и \
фонды, не индексы); для сравнения с индексом нужен compare_with_benchmark.

В history_a и history_b передайте истории двух бумаг: получите обе через get_price_history с одним и тем же \
interval и одним периодом и передайте без изменений. Облигации не сравниваются.

{_INPUT_RULES}

{_CAVEATS}"""


async def compute_price_metrics(history: HistoryResult, today: date) -> MetricsResult:
    """Метрики одной бумаги; `today` (по Москве) нужен для признака незавершённой свечи."""
    return build.metrics(validate_history(history, "history"), today)


async def compare_with_benchmark(
    history: HistoryResult, benchmark_history: HistoryResult, today: date
) -> BenchmarkComparison:
    """Бумага против эталонного индекса по общим датам."""
    series = validate_history(history, "history")
    benchmark = validate_history(benchmark_history, "benchmark_history")
    return build.benchmark(series, benchmark, today)


async def compare_securities(history_a: HistoryResult, history_b: HistoryResult, today: date) -> PairComparison:
    """Две бумаги (акции или фонды) между собой по общим датам."""
    a = validate_history(history_a, "history_a")
    b = validate_history(history_b, "history_b")
    return build.pair(a, b, today)
