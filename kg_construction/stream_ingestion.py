"""Authoritative enriched-stream ingestion into SQL and Neo4j.

One observation is committed to the SQL observation store and then projected
into Neo4j. Both writes are idempotent by the shared observation ID.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from observation_adapter import iter_jsonl
from utilities.util import get_config


class ObservationStore:
    """PostgreSQL store; uses a Timescale hypertable when available."""

    def __init__(self, connection=None):
        if connection is None:
            import psycopg2
            connection = psycopg2.connect(**get_config()["postgres_config"])
        self.connection = connection
        self.setup()

    def setup(self):
        with self.connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS urban_observations (
                    observation_id TEXT NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL,
                    end_time TIMESTAMPTZ,
                    source TEXT NOT NULL,
                    sensor TEXT NOT NULL,
                    latitude DOUBLE PRECISION,
                    longitude DOUBLE PRECISION,
                    data JSONB NOT NULL,
                    annotations JSONB NOT NULL,
                    files JSONB NOT NULL,
                    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (observation_id, observed_at)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS urban_observations_time_idx ON urban_observations(observed_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS urban_observations_source_time_idx ON urban_observations(source, observed_at)")
            # TimescaleDB requires the partitioning column in every unique key.
            # The composite key preserves replay idempotency for the immutable
            # observation timestamp while allowing genuine hypertable creation.
            try:
                cursor.execute("SAVEPOINT timescale_setup")
                cursor.execute(
                    "SELECT create_hypertable('urban_observations', 'observed_at', "
                    "if_not_exists => TRUE, migrate_data => TRUE)"
                )
                cursor.execute("RELEASE SAVEPOINT timescale_setup")
            except Exception:
                cursor.execute("ROLLBACK TO SAVEPOINT timescale_setup")
        self.connection.commit()

    @staticmethod
    def _files(record):
        return [{"name": item.name, "media_type": item.media_type, "size": item.size,
                 "sha256": item.sha256} for item in record["files"]]

    def upsert(self, record):
        with self.connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO urban_observations (
                    observation_id, observed_at, end_time, source, sensor,
                    latitude, longitude, data, annotations, files
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)
                ON CONFLICT (observation_id, observed_at) DO UPDATE SET
                    observed_at=EXCLUDED.observed_at, end_time=EXCLUDED.end_time,
                    source=EXCLUDED.source, sensor=EXCLUDED.sensor,
                    latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude,
                    data=EXCLUDED.data, annotations=EXCLUDED.annotations,
                    files=EXCLUDED.files
            """, (
                record["id"], record["time"], record.get("end_time"), record["source"],
                str(record["sensor"]), record.get("latitude"), record.get("longitude"),
                json.dumps(record["data"]), json.dumps({
                    key: record[key] for key in (
                        "event", "summary", "entities", "relations", "effects",
                        "incidents", "anomaly", "enrichment",
                    )
                }), json.dumps(self._files(record)),
            ))
        self.connection.commit()

    def close(self):
        self.connection.close()


def ingest_jsonl(path, *, sql_store=None, graph=None):
    owns_sql = sql_store is None
    owns_graph = graph is None
    sql_store = sql_store or ObservationStore()
    if graph is None:
        from kg_construction.db_manager import KGManager
        graph = KGManager(use_vectordb=True, enable_graph_reasoning=True)
    count = 0
    try:
        for record in iter_jsonl(path):
            sql_store.upsert(record)
            graph.insert_common_observation(record)
            count += 1
    finally:
        if owns_graph:
            graph.close_driver()
        if owns_sql:
            sql_store.close()
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ingest enriched Urban Observations into SIGMUS")
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args(argv)
    print(f"Ingested {ingest_jsonl(args.input)} observations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
