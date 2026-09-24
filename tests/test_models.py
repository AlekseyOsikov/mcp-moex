import pytest
from pydantic import TypeAdapter, ValidationError

from mcp_moex.errors import (
    SUPPORTED_CLASSES,
    InvalidArguments,
    IssUnavailable,
    MoexError,
    RangeTooLarge,
    SecurityNotFound,
    UnsupportedAssetClass,
)
from mcp_moex.models import (
    DateFromParam,
    HistoryResult,
    IntervalParam,
    LimitParam,
    PriceResult,
    QueryParam,
    SearchAssetTypeParam,
    SecidParam,
)


def check(annotation, value):
    return TypeAdapter(annotation).validate_python(value)


def rejected(annotation, value) -> bool:
    try:
        check(annotation, value)
    except ValidationError:
        return True
    return False


# --- 3.1 входные параметры --------------------------------------------------------------------


@pytest.mark.parametrize("value", [1, 10, 20])
def test_limit_accepts_values_in_range(value):
    assert check(LimitParam, value) == value


@pytest.mark.parametrize("value", [0, -1, 21, 100])
def test_limit_rejects_values_out_of_range(value):
    assert rejected(LimitParam, value)


def test_query_is_stripped_and_blank_is_rejected():
    assert check(QueryParam, "  Сбербанк ") == "Сбербанк"
    assert rejected(QueryParam, "")
    assert rejected(QueryParam, "   ")
    assert rejected(QueryParam, "x" * 101)


@pytest.mark.parametrize("value", ["SBER", "sber", "2xOFZ", "SBER-001D", "SU26238RMFS4"])
def test_secid_accepts_real_tickers(value):
    assert check(SecidParam, value) == value


@pytest.mark.parametrize("value", ["", "SB ER", "../x", "SBER/candles", "СБЕР", "A" * 41])
def test_secid_rejects_unsafe_or_malformed_values(value):
    assert rejected(SecidParam, value)


@pytest.mark.parametrize("value", ["2026-09-01", "1999-12-31"])
def test_date_accepts_iso_dates(value):
    assert check(DateFromParam, value) == value


@pytest.mark.parametrize("value", ["2026-9-1", "01.09.2026", "2026-09-01T00:00:00", "вчера", ""])
def test_date_rejects_other_formats(value):
    assert rejected(DateFromParam, value)


def test_enum_parameters_accept_only_documented_values():
    assert [check(SearchAssetTypeParam, v) for v in ("any", "stock", "bond", "fund", "index")] == [
        "any",
        "stock",
        "bond",
        "fund",
        "index",
    ]
    assert rejected(SearchAssetTypeParam, "futures")
    assert [check(IntervalParam, v) for v in ("hour", "day", "week", "month")] == ["hour", "day", "week", "month"]
    assert rejected(IntervalParam, "quarter")
    assert rejected(IntervalParam, "minute")


def test_parameter_schemas_describe_every_parameter():
    for annotation in (QueryParam, SearchAssetTypeParam, LimitParam, SecidParam, DateFromParam, IntervalParam):
        schema = TypeAdapter(annotation).json_schema()
        assert schema.get("description"), f"нет описания в схеме {annotation}"


# --- 3.1 выходные модели ----------------------------------------------------------------------

COMMON = {
    "secid": "X",
    "name": "Имя",
    "price": 1.0,
    "price_unit": "RUB",
    "price_source": "last_trade",
    "as_of": "2026-09-24T17:56:35+03:00",
    "delayed": True,
    "delay_minutes": 15,
    "trading_status": "T",
}
BOND_FIELDS = {"face_value", "currency", "price_rub", "accrued_interest", "dirty_price", "yield_percent"}


def test_share_and_index_prices_do_not_carry_bond_fields():
    for asset_type in ("stock", "fund", "index"):
        dumped = PriceResult(asset_type=asset_type, **COMMON).model_dump()
        assert not BOND_FIELDS & dumped.keys(), asset_type


def test_bond_price_keeps_bond_fields_even_when_yield_is_unknown():
    bond = PriceResult(
        asset_type="bond",
        face_value=1000,
        currency="RUB",
        price_rub=517.9,
        accrued_interest=22.17,
        dirty_price=540.07,
        yield_percent=None,
        **COMMON,
    )
    dumped = bond.model_dump()

    assert BOND_FIELDS <= dumped.keys()
    assert dumped["yield_percent"] is None


def test_history_result_allows_empty_period():
    empty = HistoryResult(
        secid="SBER",
        name="Сбербанк",
        asset_type="stock",
        price_unit="RUB",
        interval="day",
        date_from="2026-01-01",
        date_till="2026-01-02",
        summary=None,
        candles=[],
        candles_returned=0,
        candles_truncated=False,
        message="За период нет торгов.",
    )

    assert empty.model_dump()["summary"] is None


# --- 3.2 ошибки -------------------------------------------------------------------------------


def test_not_found_points_to_search_tool():
    error = SecurityNotFound("NOPE123")

    assert isinstance(error, MoexError)
    assert "NOPE123" in str(error)
    assert "search_securities" in str(error)


def test_unsupported_class_lists_supported_classes():
    message = str(UnsupportedAssetClass("SiZ6", "futures_forts"))

    assert "SiZ6" in message
    assert SUPPORTED_CLASSES in message
    for word in ("акции", "облигации", "фонды", "индексы"):
        assert word in message


def test_range_too_large_advises_coarser_interval_or_shorter_period():
    assert "day" in str(RangeTooLarge("hour", 5000))
    assert "5000" in str(RangeTooLarge("hour", 5000))
    assert "Сократите период" in str(RangeTooLarge("month", 5000))


def test_iss_unavailable_advises_retry():
    assert "Повторите" in str(IssUnavailable())
    assert "HTTP 503" in str(IssUnavailable("HTTP 503"))


def test_invalid_arguments_carries_its_message():
    assert str(InvalidArguments("Начало периода позже конца.")) == "Начало периода позже конца."
