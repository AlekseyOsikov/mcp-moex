"""Интервалы опроса и сводки: строка вида `15m`, `1h`, `1d` и её границы."""

import re
from dataclasses import dataclass

from ..errors import InvalidArguments

MINUTE = 60
HOUR = 60 * MINUTE
DAY = 24 * HOUR

MIN_POLL_SECONDS = 5 * MINUTE
"""Котировки и так задержаны на 15 минут, чаще опрашивать бессмысленно."""
MAX_POLL_SECONDS = DAY
MAX_REPORT_SECONDS = 7 * DAY

_UNIT_SECONDS = {"m": MINUTE, "h": HOUR, "d": DAY}
_PATTERN = re.compile(r"^(\d+)([mhd])$")
_EXAMPLES = "15m, 1h, 1d"


@dataclass(frozen=True)
class WatchInterval:
    text: str
    """Каноническая запись: без пробелов, в нижнем регистре, без ведущих нулей."""
    seconds: int


def format_seconds(seconds: int) -> str:
    """Секунды в самой крупной единице, на которую они делятся нацело: 3600 -> `1h`."""
    for unit in ("d", "h"):
        if seconds % _UNIT_SECONDS[unit] == 0:
            return f"{seconds // _UNIT_SECONDS[unit]}{unit}"
    return f"{seconds // MINUTE}m"


def parse_interval(value: str, parameter: str) -> WatchInterval:
    """Разбор `<число><m|h|d>`; другой формат (время суток, слова) отвергается с примерами."""
    match = _PATTERN.match(value.strip().lower())
    if match is None or int(match.group(1)) == 0:
        raise InvalidArguments(
            f"Параметр {parameter}: «{value}» не подходит. Укажите целое положительное число и единицу: "
            f"m (минуты), h (часы) или d (сутки), например {_EXAMPLES}. Время суток и часовые пояса не поддерживаются."
        )
    number, unit = int(match.group(1)), match.group(2)
    return WatchInterval(text=f"{number}{unit}", seconds=number * _UNIT_SECONDS[unit])


def parse_poll_interval(value: str) -> WatchInterval:
    interval = parse_interval(value, "poll_interval")
    if not MIN_POLL_SECONDS <= interval.seconds <= MAX_POLL_SECONDS:
        raise InvalidArguments(
            f"Параметр poll_interval: «{interval.text}» вне границ. Период опроса от "
            f"{format_seconds(MIN_POLL_SECONDS)} до {format_seconds(MAX_POLL_SECONDS)} включительно."
        )
    return interval


def parse_report_interval(value: str, poll: WatchInterval) -> WatchInterval:
    interval = parse_interval(value, "report_interval")
    if interval.seconds < poll.seconds:
        raise InvalidArguments(
            f"Параметр report_interval: «{interval.text}» меньше периода опроса ({poll.text}): "
            f"период сводки не может быть меньше периода опроса. Допустимо от {poll.text} до "
            f"{format_seconds(MAX_REPORT_SECONDS)}."
        )
    if interval.seconds > MAX_REPORT_SECONDS:
        raise InvalidArguments(
            f"Параметр report_interval: «{interval.text}» вне границ. Период сводки от {poll.text} "
            f"до {format_seconds(MAX_REPORT_SECONDS)} включительно."
        )
    return interval
