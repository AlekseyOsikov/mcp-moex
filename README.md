# mcp-moex

MCP-сервер над публичным API Московской биржи (ISS). Даёт ИИ-агенту поиск бумаг, текущую цену и историю цен для акций, облигаций, фондов и индексов. Ключи и регистрация не нужны, сервер работает локально.

## Инструменты

Все инструменты только читают данные (`readOnlyHint`). Схемы параметров и подробные описания сервер отдаёт клиенту через `list_tools`.

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
