import json
from unittest.mock import MagicMock

from database_storage.graph import Neo4jStore


def test_data_retains_complete_enrichment_projection():
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value.single.return_value = {"report_id": "report-1"}
    store = Neo4jStore(driver=driver, enable_reasoning=False)
    record = {
        "id": "camera-1",
        "source": "cctv",
        "sensor": "camera",
        "time": "2026-09-05T16:19:54Z",
        "data": {},
        "filepath": "/collector-data/cctv/camera.jpg",
        "raw": {"path": "/collector-data/cctv/camera.jpg"},
        "location": {"formatted_address": "Los Angeles, CA, USA", "provider": "google",
                     "provider_place_id": "place-1"},
        "summary": "Visible roadway",
        "event": {"name": "Traffic observation", "type": "traffic"},
        "entities": [],
        "relations": [],
        "effects": [{"name": "congestion", "score": 0.7}],
        "incidents": [],
        "news_incidents": [],
        "anomaly": {"score": 0.8},
        "enrichment": {"status": "completed", "provider": "openai", "model": "vision-model"},
    }

    store.insert_observation(record)

    query, parameters = session.run.call_args_list[0].args[0], session.run.call_args_list[0].kwargs
    assert "dat.annotations=$annotations_json" in query
    assert "dat.anomaly_score=$anomaly_score" in query
    assert "rep.annotations" not in query
    assert parameters["anomaly_score"] == 0.8
    assert parameters["is_anomaly"] is None
    assert parameters["filepath"] == "/collector-data/cctv/camera.jpg"
    assert parameters["confirmed_incident_names"] == []
    assert parameters["candidate_incident_names"] == []
    assert "geo.name=$location_name" in query
    assert parameters["location_name"] == "Los Angeles, CA, USA"
    annotations = json.loads(parameters["annotations_json"])
    assert annotations["entities"] == record["entities"]
    assert annotations["effects"] == record["effects"]


def test_cross_modality_candidates_are_bounded_and_news_anchored():
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value = []
    store = Neo4jStore(driver=driver, reasoner=MagicMock(), vector_store=MagicMock())

    store._link_cross_modality("report-1", {
        "id": "cctv:1", "source": "cctv", "time": "2026-09-05T17:30:00Z",
        "latitude": 34.05, "longitude": -118.24, "summary": "traffic",
    })

    query = session.run.call_args.args[0]
    parameters = session.run.call_args.kwargs
    assert "candidate.source IN $news_sources" in query
    assert "point.distance" in query
    assert parameters["max_time_seconds"] == 1800
    assert parameters["max_distance_meters"] == 20000
    assert parameters["limit"] == 6
