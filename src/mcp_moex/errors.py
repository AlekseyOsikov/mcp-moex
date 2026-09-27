"""Доменные ошибки сервера: каждая несёт текст для LLM (что случилось и что делать)."""

SUPPORTED_CLASSES = "акции, облигации, фонды и индексы"


class MoexError(Exception):
    """Базовая ошибка; `str(error)` можно показывать агенту как есть."""


class IssUnavailable(MoexError):
    """ISS не ответил или ответил ошибкой (таймаут, сеть, статус не 2xx, неразборчивый ответ)."""

    def __init__(self, detail: str = "") -> None:
        message = "Московская биржа (ISS) временно недоступна. Повторите запрос позже."
        if detail:
            message = f"{message} Подробности: {detail}"
        super().__init__(message)


class SecurityNotFound(MoexError):
    """Бумаги с таким тикером нет на бирже."""

    def __init__(self, secid: str) -> None:
        self.secid = secid
        super().__init__(
            f"Бумага с тикером «{secid}» не найдена на Московской бирже. "
            "Найдите тикер через инструмент search_securities (по названию или ISIN) и повторите запрос."
        )


class UnsupportedAssetClass(MoexError):
    """Бумага найдена, но её класс сервер не поддерживает (фьючерсы, опционы, валюта и т.п.)."""

    def __init__(self, secid: str, group: str | None = None) -> None:
        self.secid = secid
        self.group = group
        detail = f" (группа ISS: {group})" if group else ""
        super().__init__(
            f"Бумага «{secid}» относится к неподдерживаемому классу{detail}. "
            f"Сервер работает только с такими классами: {SUPPORTED_CLASSES}."
        )


class MarketDataUnavailable(MoexError):
    """Бумага известна, но биржа не отдаёт по ней котировок (не торгуется, снята с торгов)."""

    def __init__(self, secid: str) -> None:
        self.secid = secid
        super().__init__(
            f"По бумаге «{secid}» биржа не отдаёт котировок: возможно, она не торгуется или торги приостановлены. "
            "Проверьте тикер через инструмент search_securities."
        )


class RangeTooLarge(MoexError):
    """За запрошенный период слишком много свечей для обработки."""

    def __init__(self, interval: str, max_candles: int) -> None:
        self.interval = interval
        self.max_candles = max_candles
        finer = {"hour": "day, week или month", "day": "week или month", "week": "month"}.get(interval)
        advice = f"Выберите более крупный interval ({finer}) или сократите период." if finer else "Сократите период."
        super().__init__(
            f"За запрошенный период больше {max_candles} свечей с интервалом «{interval}». {advice}"
        )


class InvalidArguments(MoexError):
    """Аргументы инструмента неверны по смыслу (например, начало периода позже конца)."""


# --- Анализ переданной истории (moex-price-analytics) -------------------------------------------
# Каждая ошибка называет параметр, чья история отклонена: клиент должен знать, какую получить заново.

ANALYSIS_INTERVALS = "day, week, month"


class HistoryTruncated(MoexError):
    """В истории свечей меньше, чем за период: сводка учитывает больше, чем видно в свечах."""

    def __init__(self, param: str) -> None:
        self.param = param
        super().__init__(
            f"Параметр {param}: история усечена (candles_truncated = true), считать по неполному ряду нельзя. "
            'Получите историю через get_price_history с более крупным interval (например, "week" или "month") '
            "или за более короткий период и повторите вызов."
        )


class IntervalNotSupported(MoexError):
    """Анализ не поддерживает интервал переданной истории (часовые свечи)."""

    def __init__(self, param: str, interval: str) -> None:
        self.param = param
        self.interval = interval
        super().__init__(
            f"Параметр {param}: интервал «{interval}» для анализа не поддерживается. "
            f"Поддерживаются: {ANALYSIS_INTERVALS}. Получите историю через get_price_history с interval = "
            '"day" (короткие периоды), "week" или "month" и повторите вызов.'
        )


class NoHistoryData(MoexError):
    """В переданной истории нет свечей."""

    def __init__(self, param: str) -> None:
        self.param = param
        super().__init__(
            f"Параметр {param}: за период нет данных (свечей нет, summary = null). "
            "Возможно, торгов не было или бумага ещё не торговалась. Получите историю через get_price_history "
            "за другой период и повторите вызов."
        )


class HistoryInconsistent(MoexError):
    """Свечи не согласованы со сводкой или сами по себе неверны: история изменена или повреждена."""

    def __init__(self, param: str, detail: str) -> None:
        self.param = param
        self.detail = detail
        super().__init__(
            f"Параметр {param}: история изменена или повреждена ({detail}). "
            "Получите историю заново через get_price_history и передайте результат без изменений."
        )


class IntervalMismatch(MoexError):
    """У двух сравниваемых историй разные интервалы свечей."""

    def __init__(self, param_a: str, interval_a: str, param_b: str, interval_b: str) -> None:
        super().__init__(
            f"Интервалы историй различаются: {param_a} = «{interval_a}», {param_b} = «{interval_b}». "
            "Получите обе истории через get_price_history с одним и тем же interval и повторите вызов."
        )


class BenchmarkMustBeIndex(MoexError):
    """Эталоном в сравнении с индексом может быть только индекс."""

    def __init__(self, param: str, secid: str, asset_type: str) -> None:
        self.param = param
        super().__init__(
            f"Параметр {param}: эталоном может быть только индекс, а «{secid}» относится к классу «{asset_type}». "
            "Для сравнения двух бумаг (акций или фондов) между собой используйте compare_securities."
        )


class IndexNotAllowed(MoexError):
    """В сравнении двух бумаг индекс не допускается."""

    def __init__(self, param: str, secid: str) -> None:
        self.param = param
        super().__init__(
            f"Параметр {param}: «{secid}» это индекс, а compare_securities сравнивает только акции и фонды. "
            "Для сравнения бумаги с индексом используйте compare_with_benchmark."
        )


class BondNotComparable(MoexError):
    """Облигации в сравнениях не поддерживаются."""

    def __init__(self, param: str, secid: str) -> None:
        self.param = param
        super().__init__(
            f"Параметр {param}: «{secid}» это облигация, сравнение облигаций не поддерживается. "
            "Цена облигации выражена в процентах от номинала без накопленного купонного дохода, поэтому изменение "
            "цены не равно доходу и его нельзя сопоставлять с другими бумагами. "
            "Метрики одной облигации можно получить через compute_price_metrics."
        )


class TooFewCommonCandles(MoexError):
    """У двух историй меньше двух общих свечей."""

    def __init__(self, common: int) -> None:
        self.common = common
        super().__init__(
            f"Общий период двух историй слишком короткий: общих свечей {common}, нужно не меньше двух. "
            "Получите обе истории через get_price_history за более длинный период или с более мелким interval "
            "и повторите вызов."
        )


class WatchNotSet(MoexError):
    """Для чата не задан опрос цен (режим расписания)."""

    def __init__(self, chat_id: int) -> None:
        self.chat_id = chat_id
        super().__init__(
            f"Для чата {chat_id} опрос не задан. Задайте его инструментом watch_set "
            "(тикеры, poll_interval и report_interval) и повторите запрос."
        )


class WatchStoreUnavailable(MoexError):
    """Файл хранилища расписания не открывается или повреждён."""

    def __init__(self, detail: str = "") -> None:
        message = (
            "Хранилище расписания недоступно: файл, указанный в --watch-db, нельзя открыть как базу данных. "
            "Проверьте путь и права доступа к файлу, при необходимости укажите другой путь и перезапустите сервер."
        )
        if detail:
            message = f"{message} Подробности: {detail}"
        super().__init__(message)
