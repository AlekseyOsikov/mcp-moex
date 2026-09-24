"""Инструмент search_securities: поиск бумаги по названию, тикеру или ISIN."""

import asyncio
from typing import Any

from ..errors import InvalidArguments
from ..iss import IssClient
from ..models import AssetType, SearchAssetType, SearchResult, SecurityHit
from ..resolver import GROUP_TO_ASSET_TYPE, INDEX_BOARDS

SEARCH_COLUMNS = "secid,shortname,name,isin,group,primary_boardid"

SEARCH_TTL = 5 * 60.0
"""Результаты поиска кэшируются на несколько минут."""

ISS_SEARCH_LIMIT = 100
"""Максимум строк, которые ISS отдаёт за один поисковый запрос."""

GROUPS_BY_ASSET_TYPE: dict[AssetType, tuple[str, ...]] = {
    "stock": ("stock_shares", "stock_dr"),
    "fund": ("stock_ppif", "stock_etf"),
    "index": ("stock_index",),
    "bond": ("stock_bonds",),
}
"""Группы ISS по классам. Порядок классов задаёт порядок в `any`: облигаций много, поэтому они последние."""

EXCLUDED_BOARDS = frozenset({"INAV", "INPF"})
"""Расчётные показатели, а не бумаги: индикативная стоимость паёв (iNAV) и фиксинги."""

DESCRIPTION = """\
Поиск бумаги на Московской бирже по названию, тикеру или ISIN. Возвращает тикеры (secid), \
которые нужны инструментам get_current_price и get_price_history.

Используйте, когда пользователь назвал бумагу словами («Сбербанк», «ОФЗ 26238», «золото») или дал ISIN, \
а не тикер. Ищет только торгуемые бумаги: акции и депозитарные расписки (stock), облигации (bond), \
паи фондов (fund), индексы (index). Фьючерсы, опционы и валюта не ищутся.

Указывайте asset_type, если класс известен: у одного эмитента бывают десятки облигаций, и они вытесняют \
остальное. Название часто неоднозначно (у «Сбербанка» обыкновенные SBER и привилегированные SBERP акции, \
плюс облигации): если подходит несколько бумаг, уточните у пользователя или назовите, какую выбрали. \
Точное совпадение с тикером или ISIN идёт первым. Если truncated = true, найдено больше, чем limit: \
уточните запрос или сузьте asset_type."""


def _groups_for(asset_type: SearchAssetType) -> list[tuple[AssetType, str]]:
    classes = list(GROUPS_BY_ASSET_TYPE) if asset_type == "any" else [asset_type]
    return [(cls, group) for cls in classes for group in GROUPS_BY_ASSET_TYPE[cls]]


async def _search_group(iss: IssClient, query: str, group: str) -> list[dict[str, Any]]:
    tables = await iss.get_tables(
        "securities.json",
        {
            "q": query,
            "is_trading": 1,
            "group_by": "group",
            "group_by_filter": group,
            "limit": ISS_SEARCH_LIMIT,
            "iss.only": "securities",
            "securities.columns": SEARCH_COLUMNS,
        },
        ttl=SEARCH_TTL,
    )
    return tables.get("securities") or []


def _is_real_security(asset_type: AssetType, board: str) -> bool:
    if board in EXCLUDED_BOARDS:
        return False
    return asset_type != "index" or board in INDEX_BOARDS


def _is_exact_match(hit: SecurityHit, query: str) -> bool:
    key = query.casefold()
    return hit.secid.casefold() == key or (hit.isin or "").casefold() == key


async def search_securities(
    iss: IssClient,
    query: str,
    asset_type: SearchAssetType = "any",
    limit: int = 10,
) -> SearchResult:
    """Находит торгуемые бумаги по названию, тикеру или ISIN."""
    query = query.strip()
    if not query:
        raise InvalidArguments("Параметр query пуст: укажите название бумаги, тикер или ISIN.")

    groups = _groups_for(asset_type)
    found = await asyncio.gather(*(_search_group(iss, query, group) for _, group in groups))

    hits: list[SecurityHit] = []
    seen: set[str] = set()
    capped = False
    for (cls, _), rows in zip(groups, found, strict=True):
        capped = capped or len(rows) >= ISS_SEARCH_LIMIT
        for row in rows:
            secid, board = row["secid"], row["primary_boardid"]
            real_class = GROUP_TO_ASSET_TYPE.get(row["group"], cls)
            if secid in seen or not _is_real_security(real_class, board):
                continue
            seen.add(secid)
            hits.append(
                SecurityHit(
                    secid=secid,
                    name=row.get("name") or row.get("shortname") or secid,
                    isin=row.get("isin") or None,
                    asset_type=real_class,
                    board=board,
                )
            )

    hits.sort(key=lambda hit: not _is_exact_match(hit, query))  # стабильная: остальное в порядке ISS
    results = hits[:limit]
    return SearchResult(
        query=query,
        asset_type=asset_type,
        results=results,
        count=len(results),
        truncated=len(hits) > limit or capped,
    )
