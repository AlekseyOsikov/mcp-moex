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
