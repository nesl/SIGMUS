from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from qa.client import _system_prompt, _tool_result
from qa.query_service import QueryService, _plain


def test_query_limits_are_bounded():
    assert QueryService._limit(0) == 1
    assert QueryService._limit(12) == 12
    assert QueryService._limit(1000) == QueryService.MAX_RESULTS


def test_plain_serializes_nested_driver_values():
    value = {"items": (1, {"value": "ok"})}
    assert _plain(value) == {"items": [1, {"value": "ok"}]}


def test_plain_serializes_neo4j_style_temporal_value():
    class NeoTime:
        def iso_format(self):
            return "2026-09-05T16:19:54.000000000+00:00"

    assert _plain(NeoTime()) == "2026-09-05T16:19:54.000000000+00:00"

    service = QueryService.__new__(QueryService)
    service.display_timezone = ZoneInfo("America/Los_Angeles")
    row = service._report_time({"time": _plain(NeoTime())})
    assert row["time_local"] == "2026-09-05T09:19:54-07:00"


def test_report_time_exposes_same_instant_in_utc_and_local_time():
    service = QueryService.__new__(QueryService)
    service.display_timezone = ZoneInfo("America/Los_Angeles")

    row = service._report_time({"time": "2026-09-06T02:30:00Z"})

    assert row["time"] == "2026-09-06T02:30:00Z"
    assert row["time_utc"] == "2026-09-06T02:30:00Z"
    assert row["time_local"] == "2026-09-05T19:30:00-07:00"
    assert row["timezone"] == "America/Los_Angeles"


def test_mcp_structured_result_is_forwarded_to_llm():
    result = SimpleNamespace(structuredContent={"result": [{"observation_id": "obs-1"}]}, content=[])
    assert '"observation_id": "obs-1"' in _tool_result(result)


def test_qa_prompt_includes_authoritative_local_and_utc_clock():
    prompt = _system_prompt(
        now=datetime(2026, 9, 6, 17, 30, tzinfo=timezone.utc),
        timezone_name="America/Los_Angeles",
    )

    assert "Current UTC time: 2026-09-06T17:30:00+00:00" in prompt
    assert "Current configured-local time: 2026-09-06T10:30:00-07:00" in prompt
    assert 'Interpret "this morning" as local midnight through local noon' in prompt
    assert "Never substitute a remembered" in prompt


def test_invalid_aggregate_is_rejected_before_database_query():
    service = QueryService.__new__(QueryService)
    with pytest.raises(ValueError, match="aggregate must be"):
        service.aggregate_measurements(
            source="air", field="pm2.5_atm", start="2026-09-01T00:00:00Z",
            end="2026-09-02T00:00:00Z", aggregate="DROP TABLE"
        )


def test_related_reports_expose_relationship_meaning_and_context():
    service = QueryService.__new__(QueryService)
    service.display_timezone = ZoneInfo("America/Los_Angeles")
    service.graph = MagicMock()
    session = service.graph.session.return_value.__enter__.return_value
    session.run.return_value = [{
        "observation_id": "news:1", "source": "gdelt",
        "time": "2026-09-05T17:30:00Z", "relationship_type": "llm_corroboration",
        "confidence": .8, "reason": "matching smoke evidence", "distance_meters": 500,
        "time_delta_seconds": 60, "location_name": "Los Angeles, CA",
        "filepath": "/data/news.csv",
    }]

    rows = service.find_related_reports("cctv:1")

    query = session.run.call_args.args[0]
    assert "relationship_type" in query
    assert rows[0]["relationship_type"] == "llm_corroboration"
    assert rows[0]["time_local"] == "2026-09-05T10:30:00-07:00"
    assert rows[0]["filepath"] == "/data/news.csv"
