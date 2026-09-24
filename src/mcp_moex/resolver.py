"""Резолвер `secid` -> движок, рынок, площадка и класс актива по карточке бумаги ISS."""

from dataclasses import dataclass

from .errors import SecurityNotFound, UnsupportedAssetClass
from .iss import REFERENCE_TTL, IssClient
from .models import AssetType

GROUP_TO_ASSET_TYPE: dict[str, AssetType] = {
    "stock_shares": "stock",
    "stock_dr": "stock",
    "stock_bonds": "bond",
    "stock_ppif": "fund",
    "stock_etf": "fund",
    "stock_index": "index",
}

_CURRENCY_ALIASES = {"SUR": "RUB"}


def normalize_currency(code: str | None) -> str:
    """Код валюты ISS в привычный вид: рубль в ISS это `SUR`; пустое значение считается рублём."""
    code = code or "SUR"
    return _CURRENCY_ALIASES.get(code, code)


INDEX_BOARDS = frozenset({"SNDX", "RTSI"})
"""Площадки настоящих индексов; остальные `stock_index` (iNAV, фиксинги) это расчётные показатели."""


@dataclass(frozen=True)
class Instrument:
    secid: str
    name: str
    isin: str | None
    group: str
    asset_type: AssetType
    engine: str
    market: str
    board: str
    currency: str

    @property
    def board_path(self) -> str:
        """Путь ISS до бумаги на её основной площадке (без суффикса `.json` и разделов)."""
        return f"engines/{self.engine}/markets/{self.market}/boards/{self.board}/securities/{self.secid}"


async def resolve(iss: IssClient, secid: str) -> Instrument:
    """Находит бумагу по тикеру (регистр не важен) и определяет её основную площадку и класс.

    Raises:
        SecurityNotFound: ISS не знает такого тикера или у бумаги нет основной площадки.
        UnsupportedAssetClass: бумага не акция, облигация, фонд и не индекс.
    """
    tables = await iss.get_tables(
        f"securities/{secid}.json",
        {"iss.only": "description,boards"},
        ttl=REFERENCE_TTL,
    )
    description = {row["name"]: row["value"] for row in tables.get("description", [])}
    if not description:
        raise SecurityNotFound(secid)

    canonical = description.get("SECID") or secid
    group = description.get("GROUP") or ""
    asset_type = GROUP_TO_ASSET_TYPE.get(group)
    if asset_type is None:
        raise UnsupportedAssetClass(canonical, group or None)

    primary = [row for row in tables.get("boards", []) if row.get("is_primary") == 1]
    primary.sort(key=lambda row: row.get("engine") != "stock")  # стабильно: сначала фондовый рынок
    if not primary:
        raise SecurityNotFound(canonical)
    board = primary[0]

    if asset_type == "index" and board["boardid"] not in INDEX_BOARDS:
        raise UnsupportedAssetClass(canonical, f"{group}, площадка {board['boardid']}")

    return Instrument(
        secid=canonical,
        name=description.get("SHORTNAME") or description.get("NAME") or canonical,
        isin=description.get("ISIN"),
        group=group,
        asset_type=asset_type,
        engine=board["engine"],
        market=board["market"],
        board=board["boardid"],
        currency=normalize_currency(board.get("currencyid")),
    )
