"""One-time ingestion of enriched Urban Observation JSONL."""

from __future__ import annotations

import argparse
from pathlib import Path

from .graph import Neo4jStore
from .observation import iter_jsonl
from .timescale import TimescaleStore


def ingest_jsonl(path, *, sql_store=None, graph=None):
    owns_sql, owns_graph = sql_store is None, graph is None
    sql_store = sql_store or TimescaleStore()
    graph = graph or Neo4jStore()
    count = 0
    try:
        for record in iter_jsonl(path):
            sql_store.upsert(record)
            graph.insert_observation(record)
            count += 1
    finally:
        if owns_graph:
            graph.close()
        if owns_sql:
            sql_store.close()
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ingest enriched observations into SIGMUS")
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args(argv)
    print(f"Ingested {ingest_jsonl(args.input)} observations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
