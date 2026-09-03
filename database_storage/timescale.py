"""Idempotent PostgreSQL/TimescaleDB observation storage."""

from __future__ import annotations

import json

from .config import get_config


class TimescaleStore:
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
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS urban_observations_time_idx "
                "ON urban_observations(observed_at)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS urban_observations_source_time_idx "
                "ON urban_observations(source, observed_at)"
            )
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
        return [
            {"name": item.name, "media_type": item.media_type, "size": item.size,
             "sha256": item.sha256}
            for item in record["files"]
        ]

    def upsert(self, record):
        annotation_keys = (
            "event", "summary", "entities", "relations", "effects",
            "incidents", "news_incidents", "anomaly", "enrichment",
        )
        with self.connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO urban_observations (
                    observation_id, observed_at, end_time, source, sensor,
                    latitude, longitude, data, annotations, files
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)
                ON CONFLICT (observation_id, observed_at) DO UPDATE SET
                    end_time=EXCLUDED.end_time, source=EXCLUDED.source,
                    sensor=EXCLUDED.sensor, latitude=EXCLUDED.latitude,
                    longitude=EXCLUDED.longitude, data=EXCLUDED.data,
                    annotations=EXCLUDED.annotations, files=EXCLUDED.files
            """, (
                record["id"], record["time"], record.get("end_time"),
                record["source"], str(record["sensor"]), record.get("latitude"),
                record.get("longitude"), json.dumps(record["data"]),
                json.dumps({key: record[key] for key in annotation_keys}),
                json.dumps(self._files(record)),
            ))
        self.connection.commit()

    def close(self):
        self.connection.close()
