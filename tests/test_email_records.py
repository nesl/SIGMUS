import csv

from kg_construction.email_records import normalize_legacy, records_from_csv


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def send_message_to_llm_single(self, _prompt, temperature=0.0):
        self.calls += 1
        return ('{"timestamp":"2026-08-31T10:00:00Z","end_time":"",'
                '"location":"Los Angeles","event_name":"Fire","event_type":"fire",'
                '"description":"Smoke reported","author":"Citizen"}', None)


def test_legacy_twitter_row_does_not_require_llm():
    result = normalize_legacy({
        "email_time": "2026-08-31T10:00:00Z", "event": "Road closure",
        "location": "Main St", "start_time": "", "end_time": "", "body": "Details",
    }, "twitter")
    assert result["schema_version"] == "twitter_enriched.legacy.v1"
    assert result["timestamp"] == "2026-08-31T10:00:00Z"


def test_raw_row_is_enriched_during_ingestion(tmp_path):
    path = tmp_path / "raw.csv"
    fields = ["schema_version", "source", "imap_uid", "message_id", "received_at", "sender", "subject", "body", "ingested_at"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"schema_version": "email_raw.v1", "source": "citizen", "imap_uid": "7", "received_at": "2026-08-31T10:00:00Z", "subject": "Alert", "body": "Smoke"})
    llm = FakeLLM()
    result = list(records_from_csv(
        str(path), "citizen", llm_client=llm, allow_local_enrichment=True,
    ))
    assert llm.calls == 1
    assert result[0]["schema_version"] == "citizen_enriched.v2"
    assert result[0]["location"] == "Los Angeles"


def test_raw_row_refuses_implicit_local_enrichment(tmp_path):
    path = tmp_path / "raw.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["schema_version", "body"])
        writer.writeheader(); writer.writerow({"schema_version": "email_raw.v1", "body": "Smoke"})
    llm = FakeLLM()
    try:
        list(records_from_csv(str(path), "citizen", llm_client=llm))
        assert False, "expected shared-enrichment requirement"
    except ValueError as exc:
        assert "Urban Observations" in str(exc)
    assert llm.calls == 0
