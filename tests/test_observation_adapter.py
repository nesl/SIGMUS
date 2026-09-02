from urban_observation_model import Observation, SCHEMA_VERSION
from observation_adapter import to_sigmus_record


def test_completed_shared_enrichment_maps_without_model_or_geocoder():
    observation = Observation.from_dict({
        "schema_version": SCHEMA_VERSION, "id": "citizen:1", "source": "citizen",
        "time": "2026-09-02T12:00:00Z", "sensor": "email", "data": {"body": "fire"},
        "files": [], "annotations": {
            "enrichment": {"status": "completed", "version": "1"},
            "event": {"name": "Fire"}, "location": {"latitude": 34.0, "longitude": -118.0},
            "entities": [{"name": "LAFD", "type": "organization"}],
            "incidents": [{"name": "fire", "score": .9}],
        },
    })
    record = to_sigmus_record(observation)
    assert record["event"]["name"] == "Fire"
    assert record["latitude"] == 34.0
    assert record["entities"][0]["name"] == "LAFD"


def test_unenriched_observation_is_not_locally_interpreted():
    observation = Observation.from_dict({
        "schema_version": SCHEMA_VERSION, "id": "citizen:2", "source": "citizen",
        "time": "2026-09-02T12:00:00Z", "sensor": "email", "data": {}, "files": [],
    })
    try:
        to_sigmus_record(observation)
        assert False, "expected authoritative-enrichment requirement"
    except ValueError as exc:
        assert "enrichment service" in str(exc)
