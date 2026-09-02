from kg_construction import stream_ingestion


class FakeStore:
    def __init__(self):
        self.records = []
        self.closed = False

    def upsert(self, record):
        self.records.append(record)

    def close(self):
        self.closed = True


class FakeGraph:
    def __init__(self):
        self.records = []
        self.closed = False

    def insert_common_observation(self, record):
        self.records.append(record)

    def close_driver(self):
        self.closed = True


def test_enriched_stream_is_sent_to_sql_and_graph(monkeypatch):
    records = [{"id": "obs-1"}, {"id": "obs-2"}]
    monkeypatch.setattr(stream_ingestion, "iter_jsonl", lambda path: iter(records))
    sql = FakeStore()
    graph = FakeGraph()

    count = stream_ingestion.ingest_jsonl("unused.jsonl", sql_store=sql, graph=graph)

    assert count == 2
    assert sql.records == records
    assert graph.records == records
    # Objects supplied by callers can be reused for another batch.
    assert not sql.closed
    assert not graph.closed
