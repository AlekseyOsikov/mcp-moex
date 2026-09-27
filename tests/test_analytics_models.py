import pytest
from pydantic import BaseModel, TypeAdapter

from mcp_moex.models import (
    BenchmarkComparison,
    BenchmarkHistoryParam,
    BenchmarkPeriod,
    Drawdown,
    HistoryAParam,
    HistoryBParam,
    HistoryParam,
    MetricsResult,
    PairComparison,
    PairPeriod,
    PeriodChange,
)

RESULT_MODELS = [Drawdown, PeriodChange, BenchmarkPeriod, PairPeriod, MetricsResult, BenchmarkComparison, PairComparison]


@pytest.mark.parametrize("model", RESULT_MODELS, ids=lambda model: model.__name__)
def test_every_result_field_is_described_for_the_llm(model: type[BaseModel]):
    for name, field in model.model_fields.items():
        assert field.description, f"{model.__name__}.{name}"


def test_pair_comparison_has_no_beta_anywhere():
    schema = PairComparison.model_json_schema()

    assert "beta" not in schema["properties"]
    assert "beta" not in str(schema)
    assert "beta" in BenchmarkComparison.model_fields


@pytest.mark.parametrize("alias", [HistoryParam, BenchmarkHistoryParam, HistoryAParam, HistoryBParam])
def test_history_parameters_carry_a_description_that_points_to_get_price_history(alias):
    schema = TypeAdapter(alias).json_schema()

    assert schema["description"]
    assert "get_price_history" in schema["description"]
    assert "search_securities" in schema["description"]
