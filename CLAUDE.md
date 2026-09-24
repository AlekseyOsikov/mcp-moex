# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

MCP-сервер над публичным API Московской биржи (ISS) на Python: три инструмента только для чтения (`search_securities`, `get_current_price`, `get_price_history`) для ИИ-агента-консультанта по российскому рынку. Агент живёт в другом проекте и подключается к серверу через MCP SDK. Документация для пользователя в `README.md`, поведение инструментов в `openspec/specs/` (четыре возможности: `moex-security-search`, `moex-current-price`, `moex-price-history`, `mcp-server-runtime`).

## Команды

```bash
uv sync                                        # окружение (Python 3.12)
uv run pytest                                  # все тесты, без сети (~6 с)
uv run pytest tests/test_price.py::test_bond_rub_price_has_no_float_noise   # один тест
uv run python scripts/smoke_client.py          # сквозная проверка на живом ISS: код 0 при успехе
uv run mcp-moex                                # сервер по stdio (по умолчанию)
uv run mcp-moex --transport streamable-http --port 8000   # HTTP, только 127.0.0.1
RECORD_FIXTURES=1 uv run pytest                # дописать недостающие фикстуры ISS с живого сервера
uv run python -m tests.record_fixtures         # записать заранее заданный список запросов
```

Линтер и форматтер не настроены.

## Архитектура

Поток вызова: инструмент (`server.py`) -> `tools/*.py` -> `resolver.resolve(secid)` -> `IssClient.get_tables(...)` -> модель Pydantic -> `structuredContent` и текстовый JSON.

- **`server.py`**: `create_server(iss=None)` собирает `MCPServer` и замыкает инструменты на один `IssClient`; тесты подставляют клиент с фикстурами. `_guard` превращает `MoexError` в `ToolError(текст)`: агент видит наш русский текст, а любое другое исключение SDK скрывает без деталей. `main()` выбирает транспорт; хост HTTP жёстко `127.0.0.1`.
- **`tools/*.py`**: чистые async-функции `(iss, ...) -> модель`, без знания про MCP. Рядом лежит `DESCRIPTION` инструмента; это часть контракта для LLM, тесты проверяют его содержимое (задержка, единицы, предупреждение о сплитах, отсылка к `search_securities`).
- **`resolver.py`**: по карточке бумаги `/securities/{secid}.json` находит основную площадку (`is_primary = 1`, предпочтительно движок `stock`) и класс актива по группе ISS (`GROUP_TO_ASSET_TYPE`). Агент и инструменты не знают про движки, рынки и площадки. Расчётные показатели (iNAV, фиксинги) классом `index` не считаются.
- **`iss.py`**: единственная точка сети. Разбор `columns`/`data` в словари, повторы на сетевых сбоях, 5xx и 429, кэш в памяти по `ttl` (котировки 10 с, карточки бумаг 6 ч, поиск 5 мин). Всё остальное чистое, поэтому тесты подменяют только транспорт `httpx`.
- **`models.py`**: входные параметры как `Annotated`-псевдонимы с `Field(description=...)` (из них MCP SDK строит JSON-схему для агента) и выходные модели. `PriceResult` через `model_serializer` убирает поля облигаций у не-облигаций, но оставляет `yield_percent: null` у облигаций.
- **`errors.py`**: доменные ошибки, текст каждой рассчитан на LLM (что случилось и что делать).

Изменения поведения инструментов начинаются со спецификации: проект ведётся по OpenSpec (`/opsx:explore`, `/opsx:propose`, `/opsx:apply`, `/opsx:archive`), действующие спецификации лежат в `openspec/specs/`, активные изменения в `openspec/changes/`, завершённые в `openspec/changes/archive/` (там же дизайн и решения первой реализации, `2026-09-25-add-moex-mcp-server`). Расхождение кода и спецификации нужно править в обоих местах.

## Неочевидное

- **`mcp` 2.x, а не 1.x**: `FastMCP` переименован в `MCPServer` (`mcp.server.mcpserver`); поля результатов snake_case (`structured_content`, `is_error`, `input_schema`, `ToolAnnotations(read_only_hint=...)`). В клиентском коде проекта агента поля могут быть camelCase (mcp 1.x), сервер совместим с обоими.
- **stdout по stdio принадлежит протоколу**: `print` в пакете запрещён (проверяется тестом), логи идут в stderr (`configure_logging`).
- **Клиент ISS с `trust_env=False`**: на машине разработки задан `ALL_PROXY=socks://…`, который `httpx` не принимает, а ISS доступен напрямую.
- **Тесты и фикстуры**: `tests/conftest.py` запрещает сетевые соединения. Имя файла фикстуры это путь запроса плюс хеш отсортированных параметров, поэтому любое изменение параметров запроса к ISS даёт `MissingFixture`: перезапишите фикстуры (`RECORD_FIXTURES=1`). Список того, что записывается заранее, в `tests/record_fixtures.py`; `SEARCH_COLUMNS` и лимит поиска берутся из `tools/search.py`. Для правки ответов есть `MutatingTransport` и `set_column` в `tests/fake_iss.py`.
- **Клиент MCP в тестах открывайте внутри тела теста** (`async with connect() as client`), не в async-фикстуре: контекст `anyio` привязан к задаче, и teardown падает.
- **Особенности ISS, от которых зависит код**: неизвестный тикер даёт `HTTP 200` с пустыми таблицами; цены облигаций (и в котировках, и в свечах) в процентах от номинала, код рубля `SUR`; котировки акций, облигаций и фондов задержаны на 15 минут, индексы нет; свечи не больше 500 за запрос (`start` для страниц), `till` включает весь день, недельные и месячные свечи выбираются по дате начала; история не скорректирована на сплиты.
