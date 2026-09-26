# mcp-moex

MCP-сервер над публичным API Московской биржи (ISS). Даёт ИИ-агенту поиск бумаг, текущую цену и историю цен для акций, облигаций, фондов и индексов. Ключи и регистрация не нужны, сервер работает локально.

## Инструменты

По умолчанию инструментов три, и все только читают данные (`readOnlyHint`). Ещё шесть служебных инструментов расписания появляются только в режиме `--watch-db` (см. «Режим расписания»). Схемы параметров и подробные описания сервер отдаёт клиенту через `list_tools`.

| Инструмент | Для чего | Параметры |
|---|---|---|
| `search_securities` | Найти тикер по названию, тикеру или ISIN | `query`; `asset_type`: `any` (по умолчанию), `stock`, `bond`, `fund`, `index`; `limit`: 1–20 (по умолчанию 10) |
| `get_current_price` | Актуальная цена бумаги | `secid` (тикер, регистр не важен) |
| `get_price_history` | Свечи за период и готовая сводка | `secid`; `date_from` (`YYYY-MM-DD`); `date_till` (по умолчанию сегодня по Москве); `interval`: `hour`, `day` (по умолчанию), `week`, `month` |

Обычный порядок для агента: пользователь назвал бумагу словами, значит `search_securities`, затем `get_current_price` или `get_price_history` с найденным `secid`.

## Формат результатов

Успешный вызов возвращает `structuredContent` (по схеме инструмента) и тот же JSON текстом в `content`. Поля названы понятно, сырые таблицы ISS наружу не попадают.

**Единицы цен** (`price_unit`):

| Класс | `price_unit` | Что это |
|---|---|---|
| акции, фонды | `RUB` (или код другой валюты) | цена одной бумаги |
| облигации | `percent_of_face` | **проценты от номинала**, не рубли |
| индексы | `points` | пункты |

**Облигации.** В `get_current_price` уже посчитаны `price_rub` (цена в валюте номинала без НКД), `accrued_interest` (НКД), `dirty_price` (полная цена с НКД) и `yield_percent` (доходность к погашению, % годовых). В истории облигации остаются в процентах от номинала.

**Задержка.** Котировки акций, облигаций и фондов задержаны на 15 минут (`delayed = true`, `delay_minutes = 15`); индексы публикуются без задержки. `as_of` это время котировки по Москве (`+03:00`), а не время запроса. Если сегодня сделок не было, `price_source = "previous_close"`.

**История.** В `summary` считаются по всем свечам периода: начало, конец, минимум, максимум и изменение в %. В `candles` приходят не более 300 последних свечей (`candles_truncated`). Слишком большой запрос (больше 5000 свечей) даёт ошибку с советом выбрать более крупный `interval`.

**Ошибки** приходят как результат инструмента с `isError = true` и понятным текстом: что случилось и что делать (неизвестный тикер, неподдерживаемый класс, неверные аргументы, ISS недоступен, слишком большой период).

## Режим расписания

Сервер умеет накапливать цены во времени: клиент задаёт для чата тикеры и периоды, сервер по его вызовам опрашивает биржу, хранит замеры в SQLite и готовит сводки (агрегаты и шаблонный русский текст, без LLM). Сервер пассивен: он не измеряет время сам, а выполняет всё, срок чего наступил, когда клиент вызывает `watch_run_due`. Отправка сообщений и таймер остаются у клиента (бот).

Режим включается явно, с любым транспортом:

```bash
uv run mcp-moex --watch-db /var/lib/mcp-moex/watch.db   # файл и каталоги создаются при первом использовании
```

Без флага сервер работает как раньше: три инструмента чтения, файлов на диске нет. Файл хранилища содержит идентификаторы чатов, не кладите его в git.

**Инструменты расписания** служебные, для клиента-планировщика, а не для модели. Они принимают `chat_id` извне и изменяют хранилище, поэтому **не помечены `readOnlyHint`** (включая `watch_status` и `watch_get_report`, которые ничего не меняют): клиент, отдающий модели только инструменты с этим признаком, их не отдаст. Сервер без флага их вообще не публикует.

| Инструмент | Вход | Результат |
|---|---|---|
| `watch_set` | `chat_id`, `secids` (1–10), `poll_interval`, `report_interval` | `{chat_id, secids, poll_interval, report_interval, replaced, next_poll_at, next_report_at, first_samples: [{secid, price, price_unit, quote_at}]}` |
| `watch_stop` | `chat_id` | `{chat_id, stopped}` |
| `watch_status` | `chat_id` | `{active: false}` или `{active: true, chat_id, secids, poll_interval, report_interval, started_at, next_poll_at, next_report_at, samples_in_period, pending_reports}` |
| `watch_get_report` | `chat_id` | `Report` с `report_id = null`: сводка по накопленному, без замеров и без сдвига сроков |
| `watch_run_due` | нет | `{now, next_due_at, polled: {chats, tickers, failed}, reports: [Report], has_more}` |
| `watch_ack` | `report_ids` | `{acknowledged}` |

`Report = {report_id, chat_id, period_start, period_end, text, tickers: [{secid, samples, failed_samples, first_price, last_price, change_percent, min_price, max_price, price_unit, last_quote_at}]}`; поля с ценами `null`, если замеров нет. `text` это готовая сводка: отправляйте как есть. Все времена в ISO 8601 со смещением `+03:00`, `chat_id` целое число.

**Интервалы:** число и единица `m`, `h` или `d` (`15m`, `1h`, `1d`), время суток не поддерживается. Период опроса от `5m` до `1d`, период сводки от периода опроса до `7d`, тикеров на чат от 1 до 10. Замеры и сводки хранятся 30 суток.

**Как клиент работает с сервером:**

1. `watch_set` при команде пользователя, `watch_stop` для остановки; опрос чата один, новый заменяет прежний.
2. Таймер клиента срабатывает в момент `next_due_at` из последнего ответа `watch_run_due` и вызывает его снова; `next_due_at = null` значит, что опросов нет и таймер не нужен. Пропущенные из-за простоя замеры не догоняются: за простой делается один замер и одна сводка.
3. Сводки из `reports` клиент отправляет в чаты и после успешной отправки вызывает `watch_ack` с их `report_id`. Неподтверждённые сводки приходят снова, поэтому сбой отправки их не теряет. Пока `has_more = true`, `watch_run_due` вызывают ещё раз без ожидания (за вызов не больше 100 сводок).
4. На «Forbidden» от Telegram (бот заблокирован) клиент вызывает `watch_stop` для чата.

```python
await session.call_tool("watch_set", {"chat_id": 42, "secids": ["SBER", "SU26238RMFS4"], "poll_interval": "15m", "report_interval": "1h"})
due = json.loads((await session.call_tool("watch_run_due", {})).content[0].text)
for report in due["reports"]:
    await send_to_chat(report["chat_id"], report["text"])
    await session.call_tool("watch_ack", {"report_ids": [report["report_id"]]})
# следующий вызов watch_run_due по таймеру в due["next_due_at"]
```

Пример сводки:

```
Сводка за 26.09 12:00 - 13:00 (МСК)
SBER: 4 замера, 301,2 -> 303,0 (+0,6%), мин 300,9, макс 303,4, котировка на 12:45
SU26238RMFS4: данных нет (неудавшихся замеров: 4)
Котировки акций, облигаций и фондов задержаны на 15 минут, индексы без задержки. Это не инвестиционная рекомендация.
```

Сбой биржи по тикеру не ошибка вызова: он попадает в сводку как неудавшийся замер. Ошибки (неверный интервал, неизвестный тикер, опрос не задан, недоступное хранилище) приходят как результат с `isError = true` и русским текстом. Несколько процессов сервера могут работать с одним файлом одновременно (клиент запускает сервер на каждое обращение): согласованность держится на транзакциях SQLite. Фильтрации по торговым часам нет: вне сессии замеры сохраняются, цена просто не меняется (время последней котировки видно в сводке).

## Запуск

По умолчанию сервер работает по `stdio`: клиент сам запускает его как подпроцесс.

```bash
uv run --directory /media/alexey/DATA/Projects/Python/mcp-moex mcp-moex
```

Для отладки можно запустить его по Streamable HTTP, только на `127.0.0.1`:

```bash
uv run mcp-moex --transport streamable-http --port 8000   # http://127.0.0.1:8000/mcp
```

Журнал пишется в stderr; при `stdio` в stdout идёт только протокол.

## Подключение из проекта агента

Пример на MCP SDK для Python; работает с `mcp` 1.x и 2.x (проверено на 1.30 и 2.2).

```python
import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MCP_MOEX_DIR = "/media/alexey/DATA/Projects/Python/mcp-moex"


async def main() -> None:
    server = StdioServerParameters(command="uv", args=["run", "--directory", MCP_MOEX_DIR, "mcp-moex"])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Описания и схемы параметров для LLM: name, description, inputSchema (в mcp 2.x input_schema).
            tools = await session.list_tools()
            print([tool.name for tool in tools.tools])

            found = await session.call_tool("search_securities", {"query": "Сбербанк", "asset_type": "stock"})
            secid = json.loads(found.content[0].text)["results"][0]["secid"]

            price = await session.call_tool("get_current_price", {"secid": secid})
            print(json.loads(price.content[0].text))  # либо price.structuredContent (в mcp 2.x structured_content)


asyncio.run(main())
```

## Проверка

```bash
uv run python scripts/smoke_client.py   # запускает сервер и вызывает все три инструмента на реальном ISS; код 0 при успехе
uv run python scripts/smoke_watch.py    # то же для режима расписания, во временной БД
uv run pytest                           # тесты без сети, на записанных ответах ISS
```

## Ограничения

- Данные ISS без подписки: задержка 15 минут для акций, облигаций и фондов.
- Поддерживаются только акции (и депозитарные расписки), облигации, фонды и индексы. Фьючерсы, опционы и валюта нет.
- **Цены истории не скорректированы на сплиты, консолидации и дивидендные гэпы.** Для периода со сплитом (например, BELU в 2024 году, 1:8) изменение за период, минимум и максимум искажены.
- Недельные и месячные свечи попадают в выборку по дате начала.
- Клиент ISS не использует прокси из окружения.

## Разработка

- Тесты работают только на записанных ответах ISS в `tests/fixtures/`; сетевые соединения в них запрещены. Записать недостающие: `RECORD_FIXTURES=1 uv run pytest` или `uv run python -m tests.record_fixtures`.
- Спецификации поведения лежат в `openspec/specs/`; дизайн и решения первой реализации в `openspec/changes/archive/2026-09-25-add-moex-mcp-server/`.
