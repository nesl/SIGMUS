import json

from kg_construction.stream_worker import StreamWorker


class Store:
    def __init__(self): self.ids = []
    def upsert(self, record): self.ids.append(record["id"])


class Graph:
    def __init__(self): self.ids = []
    def insert_common_observation(self, record): self.ids.append(record["id"])


def record(identifier="worker-test"):
    return {
        "schema_version": "urban-observation.v1", "id": identifier,
        "source": "air", "time": "2026-09-02T00:00:00Z", "sensor": "one",
        "data": {"pm2.5_atm": 12.0}, "files": [],
        "annotations": {"enrichment": {"status": "completed"}},
    }


def test_worker_resumes_after_last_complete_record(tmp_path):
    source = tmp_path / "observations.jsonl"
    state = tmp_path / "offset.json"
    first = json.dumps(record("one")) + "\n"
    source.write_text(first + json.dumps(record("partial"))[:20], encoding="utf-8")
    store, graph = Store(), Graph()
    worker = StreamWorker(source, state, 0, sql_store=store, graph=graph)

    assert worker.run_once() == 1
    assert store.ids == ["one"]
    assert json.loads(state.read_text())["offset"] == len(first.encode())

    source.write_text(first + json.dumps(record("two")) + "\n", encoding="utf-8")
    assert worker.run_once() == 1
    assert store.ids == ["one", "two"]
