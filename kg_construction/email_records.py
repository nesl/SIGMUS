"""Compatibility and LLM normalization for email-backed observations.

Acquisition stores ``email_raw.v1``.  Historical enriched CSVs remain valid
and bypass the LLM; only raw records are interpreted during SIGMUS ingestion.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

import pandas as pd


RAW_SCHEMA = "email_raw.v1"


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _ci(row: Mapping[str, Any], *names: str) -> str:
    lookup = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        value = _text(lookup.get(name.lower()))
        if value:
            return value
    return ""


def is_raw_email(row: Mapping[str, Any]) -> bool:
    return _ci(row, "schema_version") == RAW_SCHEMA


def _json_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM response did not contain a JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM response JSON was not an object")
    return value


def normalize_legacy(row: Mapping[str, Any], source: str) -> dict[str, str]:
    if source == "twitter":
        received = _ci(row, "email_time", "received_at", "date")
        return {
            "schema_version": "twitter_enriched.legacy.v1",
            "source": source,
            "timestamp": _ci(row, "start_time") or received,
            "end_time": _ci(row, "end_time") or received,
            "location": _ci(row, "location"),
            "event_name": _ci(row, "event"),
            "event_type": _ci(row, "event"),
            "description": _ci(row, "body"),
            "author": _ci(row, "author"),
        }
    received = _ci(row, "Original Date", "received_at", "Timestamp")
    return {
        "schema_version": "citizen_enriched.legacy.v1",
        "source": source,
        "timestamp": _ci(row, "Timestamp") or received,
        "end_time": _ci(row, "Timestamp") or received,
        "location": _ci(row, "Location"),
        "event_name": _ci(row, "Event Name"),
        "event_type": _ci(row, "Event Type"),
        "description": _ci(row, "Description"),
        "author": "",
    }


def normalize_raw(row: Mapping[str, Any], source: str, llm_client: Any) -> dict[str, str]:
    received = _ci(row, "received_at")
    prompt = f"""You normalize a raw {source} notification email for SIGMUS ingestion.
Return exactly one JSON object with string fields: timestamp, end_time, location,
event_name, event_type, description, author. Use the received timestamp when an
event time is absent. Do not invent a location; use an empty string if absent.

Received: {received}
Sender: {_ci(row, 'sender')}
Subject: {_ci(row, 'subject')}
Body:
{_ci(row, 'body')}
"""
    response, _thoughts = llm_client.send_message_to_llm_single(prompt, temperature=0.0)
    parsed = _json_object(response or "")
    return {
        "schema_version": f"{source}_enriched.v2",
        "source": source,
        "timestamp": _text(parsed.get("timestamp")) or received,
        "end_time": _text(parsed.get("end_time")) or _text(parsed.get("timestamp")) or received,
        "location": _text(parsed.get("location")),
        "event_name": _text(parsed.get("event_name")),
        "event_type": _text(parsed.get("event_type")),
        "description": _text(parsed.get("description")) or _ci(row, "body"),
        "author": _text(parsed.get("author")),
    }


def records_from_csv(path: str, source: str, llm_client: Any | None = None,
                     llm_client_factory: Any | None = None, *,
                     allow_local_enrichment: bool = False) -> Iterable[dict[str, str]]:
    for row in pd.read_csv(path).to_dict(orient="records"):
        if is_raw_email(row):
            if not allow_local_enrichment:
                raise ValueError(
                    f"{path} contains raw email records; convert and enrich them with "
                    "Urban Observations instead of running SIGMUS-local enrichment"
                )
            if llm_client is None and llm_client_factory is not None:
                llm_client = llm_client_factory()
            if llm_client is None:
                raise ValueError(f"{path} contains raw email records but no LLM client was provided")
            yield normalize_raw(row, source, llm_client)
        else:
            yield normalize_legacy(row, source)
