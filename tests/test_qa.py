from types import SimpleNamespace

import pytest

from qa.client import _tool_result
from qa.query_service import QueryService, _plain


def test_query_limits_are_bounded():
    assert QueryService._limit(0) == 1
    assert QueryService._limit(12) == 12
    assert QueryService._limit(1000) == QueryService.MAX_RESULTS


def test_plain_serializes_nested_driver_values():
    value = {"items": (1, {"value": "ok"})}
    assert _plain(value) == {"items": [1, {"value": "ok"}]}


def test_mcp_structured_result_is_forwarded_to_llm():
    result = SimpleNamespace(structuredContent={"result": [{"observation_id": "obs-1"}]}, content=[])
    assert '"observation_id": "obs-1"' in _tool_result(result)


def test_invalid_aggregate_is_rejected_before_database_query():
    service = QueryService.__new__(QueryService)
    with pytest.raises(ValueError, match="aggregate must be"):
        service.aggregate_measurements(
            source="air", field="pm2.5_atm", start="2026-09-01T00:00:00Z",
            end="2026-09-02T00:00:00Z", aggregate="DROP TABLE"
        )
