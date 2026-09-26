"""Инструменты расписания: описания для клиента-планировщика и тонкие функции к сервису.

Инструменты служебные: их вызывает клиент (бот-планировщик), а не модель, поэтому описания говорят с клиентом.
"""

from collections.abc import Sequence

from ..models import AckResult, Report, RunDueResult, WatchSetResult, WatchStatusResult, WatchStopResult
from ..watch.service import WatchService

_SERVICE_NOTE = (
    "СЛУЖЕБНЫЙ инструмент для клиента-планировщика, а не для ответов пользователю: не вызывайте его по просьбе "
    "пользователя напрямую и не подставляйте чужой chat_id."
)

_INTERVALS = (
    "Интервалы это целое число и единица: m (минуты), h (часы), d (сутки), например 15m, 1h, 1d; время суток "
    "не поддерживается. Период опроса от 5m до 1d, период сводки от периода опроса до 7d."
)

SET_DESCRIPTION = f"""\
Задать или заменить периодический опрос цен для чата: тикеры (от 1 до 10), период опроса poll_interval и период \
сводки report_interval. {_SERVICE_NOTE}

{_INTERVALS} Сразу делается первый замер по каждому тикеру: он же проверяет тикеры (неизвестный тикер, бумага без \
котировок или недоступность биржи дают ошибку, и прежний опрос чата остаётся без изменений). Опрос чата один: новый \
заменяет прежний (replaced = true), замеры прежнего опроса в новые сводки не входят, а неподтверждённые сводки \
прежнего опроса остаются доступными. Сервер сам ничего не опрашивает по времени: замеры делаются, когда клиент \
вызывает watch_run_due. Котировки задержаны на 15 минут."""

STOP_DESCRIPTION = f"""\
Остановить опрос чата и удалить его замеры и неподтверждённые сводки. Повторный вызов и вызов для чата без опроса \
не ошибка (stopped = false). {_SERVICE_NOTE}"""

STATUS_DESCRIPTION = f"""\
Состояние опроса чата: active = false, если опроса нет; иначе тикеры, интервалы, время следующего опроса и сводки \
(ISO 8601, +03:00), число замеров текущего периода и число неподтверждённых сводок. Ничего не меняет. {_SERVICE_NOTE}"""

GET_REPORT_DESCRIPTION = f"""\
Сводка по уже накопленным замерам текущего периода чата (с прошлой сводки или постановки опроса до сейчас) в том \
же виде, что и сводки из watch_run_due, но с report_id = null. Замеров не делает, ничего не сохраняет и не сдвигает \
сроки: подтверждать через watch_ack её не нужно. Если опрос не задан, вернёт ошибку. {_SERVICE_NOTE}"""

RUN_DUE_DESCRIPTION = f"""\
Выполнить всё, срок чего наступил к моменту вызова, по всем чатам: опросить биржу по тикерам, у которых пора \
делать замер, и сформировать сводки, у которых пора. Сервер сам время не отсчитывает, поэтому клиент вызывает \
этот инструмент по таймеру: точно в момент next_due_at из ответа (ближайший будущий срок опроса или сводки; null, \
если опросов нет, тогда таймер не нужен) и переставляет таймер по каждому ответу. {_SERVICE_NOTE}

В ответе reports: сводки к отправке (готовый русский text отправляйте как есть), включая ранее не подтверждённые. \
Сводка считается доставленной только после watch_ack: вызывайте watch_ack с report_id после успешной отправки, иначе \
сводка придёт снова при следующем вызове. Если has_more = true, вызовите инструмент ещё раз без ожидания. Пропущенные \
из-за простоя замеры не догоняются: за пропущенное время делается один замер и одна сводка. Сбой биржи по тикеру не \
ошибка: он учитывается в сводке как неудавшийся замер (polled.failed)."""

ACK_DESCRIPTION = f"""\
Подтвердить доставку сводок: report_ids это report_id сводок из watch_run_due, которые успешно отправлены в чат. \
Подтверждённые сводки больше не возвращаются. Идемпотентен: повторное подтверждение и неизвестный report_id не \
ошибка, acknowledged показывает, сколько сводок отмечено на самом деле. Вызывайте только после успешной \
отправки. {_SERVICE_NOTE}"""


async def watch_set(
    service: WatchService, chat_id: int, secids: Sequence[str], poll_interval: str, report_interval: str
) -> WatchSetResult:
    return await service.set(chat_id, secids, poll_interval, report_interval)


async def watch_stop(service: WatchService, chat_id: int) -> WatchStopResult:
    return await service.stop(chat_id)


async def watch_status(service: WatchService, chat_id: int) -> WatchStatusResult:
    return await service.status(chat_id)


async def watch_get_report(service: WatchService, chat_id: int) -> Report:
    return await service.get_report(chat_id)


async def watch_run_due(service: WatchService) -> RunDueResult:
    return await service.run_due()


async def watch_ack(service: WatchService, report_ids: Sequence[int]) -> AckResult:
    return await service.ack(report_ids)
