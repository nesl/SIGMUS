"""Bounded, read-only queries shared by MCP and deterministic tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2
from neo4j import GraphDatabase

from database_storage.config import get_config


def _plain(value: Any) -> Any:
    """Convert driver-specific values into JSON-compatible values."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    # Neo4j temporal values intentionally mirror datetime without subclassing
    # it. Convert them before the generic mapping/sequence handling below.
    iso_format = getattr(value, "iso_format", None)
    if callable(iso_format):
        return iso_format()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "items"):
        return {str(key): _plain(item) for key, item in dict(value).items()}
    return value


class QueryService:
    """Read-only access to the graph, observations, and optional vector index."""

    MAX_RESULTS = 100
    BUCKETS = {"5 minutes", "15 minutes", "1 hour", "6 hours", "1 day"}
    AGGREGATES = {"avg": "AVG", "min": "MIN", "max": "MAX", "sum": "SUM", "count": "COUNT"}
    SOURCE_ALIASES = {
        "air_quality": "air", "air_quality_sensor": "air", "purpleair": "air",
        "alert_california": "alertcalifornia", "citizenapp": "citizen",
        "openweather": "weather", "openweathermap": "weather",
    }
    FIELD_ALIASES = {
        "pm2_5": "pm2.5_atm", "pm2.5": "pm2.5_atm", "pm25": "pm2.5_atm",
        "wind_speed_mps": "wind_speed", "wind_deg": "wind_direction",
    }

    def __init__(self, *, sql_connection=None, graph_driver=None, vector_client=None):
        config = get_config()
        self.display_timezone = ZoneInfo(config.get("timezone", "America/Los_Angeles"))
        self.sql = sql_connection or psycopg2.connect(**config["postgres_config"])
        neo = config["neo4j_config"]
        self.graph = graph_driver or GraphDatabase.driver(
            neo["uri"], auth=(neo["username"], neo["password"])
        )
        self.vector = vector_client
        if self.vector is None and config.get("marqo_config", {}).get("uri"):
            try:
                import marqo
                self.vector = marqo.Client(url=config["marqo_config"]["uri"])
            except Exception:
                self.vector = None

    @staticmethod
    def _limit(limit: int) -> int:
        return max(1, min(int(limit), QueryService.MAX_RESULTS))

    @classmethod
    def _source(cls, source: str) -> str:
        return cls.SOURCE_ALIASES.get(source.lower(), source.lower())

    @classmethod
    def _field(cls, field: str) -> str:
        return cls.FIELD_ALIASES.get(field.lower(), field)

    def _report_time(self, row: dict) -> dict:
        """Expose one canonical instant in UTC and the configured display zone."""
        text = str(row.get("time") or "")
        if not text:
            return row
        try:
            # Neo4j renders nanoseconds; Python 3.10 accepts at most six
            # fractional digits in fromisoformat.
            parseable = re.sub(r"(\.\d{6})\d+(?=(?:[+-]\d\d:\d\d|Z)$)", r"\1", text)
            instant = datetime.fromisoformat(parseable.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                # Legacy records were produced from UTC-normalized readers but
                # stored without a type marker. Treat them as UTC explicitly.
                instant = instant.replace(tzinfo=timezone.utc)
        except ValueError:
            return row
        utc = instant.astimezone(timezone.utc)
        row["time"] = utc.isoformat().replace("+00:00", "Z")
        row["time_utc"] = row["time"]
        row["time_local"] = instant.astimezone(self.display_timezone).isoformat()
        row["timezone"] = self.display_timezone.key
        return row

    @contextmanager
    def _cursor(self):
        previous = self.sql.autocommit
        self.sql.autocommit = False
        cursor = self.sql.cursor()
        try:
            cursor.execute("BEGIN READ ONLY")
            yield cursor
        finally:
            self.sql.rollback()
            cursor.close()
            self.sql.autocommit = previous

    def search_reports(self, *, source: str | None = None, start: str | None = None,
                       end: str | None = None, event_type: str | None = None,
                       text: str | None = None, anomalous: bool | None = None,
                       limit: int = 20) -> list[dict]:
        rows = []
        with self.graph.session() as session:
            result = session.run("""
                MATCH (r:Report)
                MATCH (r)-[:HAS_DATA]->(d:Data)
                WHERE r.observation_id IS NOT NULL
                  AND ($source IS NULL OR r.source = $source)
                  AND ($start IS NULL OR datetime(r.time) >= datetime($start))
                  AND ($end IS NULL OR datetime(r.time) <= datetime($end))
                  AND ($event_type IS NULL OR toLower(r.event_type) = toLower($event_type))
                  AND ($text IS NULL OR all(token IN split(toLower($text), ' ')
                       WHERE toLower(coalesce(r.summary,'')) CONTAINS token))
                  AND ($anomalous IS NULL OR coalesce(d.is_anomaly, false) = $anomalous)
                OPTIONAL MATCH (r)-[:OCCURRED_AT]->(g:GeoEntity)
                RETURN r.observation_id AS observation_id, r.source AS source,
                       r.time AS time, r.summary AS summary, r.event_name AS event_name,
                       r.event_type AS event_type, r.latitude AS latitude,
                       r.longitude AS longitude, r.incident_names AS incidents,
                       r.candidate_incident_names AS candidate_incidents,
                       d.anomaly_score AS anomaly_score, d.is_anomaly AS is_anomaly,
                       d.enrichment_status AS enrichment_status,
                       g.name AS location_name, d.filepath AS filepath
                ORDER BY datetime(r.time) DESC,
                         CASE WHEN g.name IS NULL OR trim(g.name) = '' THEN 1 ELSE 0 END,
                         r.observation_id
                LIMIT $limit
            """, source=source, start=start, end=end, event_type=event_type,
                text=text, anomalous=anomalous, limit=self._limit(limit))
            rows = [self._report_time(_plain(dict(row))) for row in result]
        return rows

    def get_report(self, observation_id: str) -> dict | None:
        with self.graph.session() as session:
            row = session.run("""
                MATCH (r:Report {observation_id:$observation_id})
                OPTIONAL MATCH (o:Observer)-[:HAS_REPORT]->(r)
                OPTIONAL MATCH (r)-[:HAS_DATA]->(d:Data)
                OPTIONAL MATCH (r)-[:MENTIONS]->(a:Actor)
                OPTIONAL MATCH (r)-[:HAS_LABEL]->(i:Incident)
                OPTIONAL MATCH (r)-[:OCCURRED_AT]->(g:GeoEntity)
                RETURN properties(r) AS report, properties(o) AS observer,
                       collect(DISTINCT properties(d)) AS data,
                       collect(DISTINCT properties(a)) AS actors,
                       collect(DISTINCT properties(i)) AS incidents,
                       collect(DISTINCT properties(g)) AS locations
            """, observation_id=observation_id).single()
        return _plain(dict(row)) if row else None

    def find_related_reports(self, observation_id: str, limit: int = 20,
                             different_source: bool = False) -> list[dict]:
        with self.graph.session() as session:
            result = session.run("""
                MATCH (origin:Report {observation_id:$observation_id})
                MATCH path=(origin)-[:HAS_LABEL|MENTIONS|CORROBORATES*1..2]-(related:Report)
                WHERE related <> origin AND related.observation_id IS NOT NULL
                  AND (NOT $different_source OR related.source <> origin.source)
                WITH related, path,
                     head([node IN nodes(path) WHERE node:LLM_CONTEXT]) AS context
                OPTIONAL MATCH (related)-[:HAS_DATA]->(data:Data)
                OPTIONAL MATCH (related)-[:OCCURRED_AT]->(geo:GeoEntity)
                RETURN DISTINCT related.observation_id AS observation_id,
                       related.source AS source, related.time AS time,
                       related.summary AS summary, related.event_type AS event_type,
                       CASE
                         WHEN context IS NOT NULL THEN 'llm_corroboration'
                         WHEN any(edge IN relationships(path) WHERE type(edge)='HAS_LABEL')
                           THEN 'shared_confirmed_incident'
                         ELSE 'shared_actor'
                       END AS relationship_type,
                       context.confidence AS confidence, context.reason AS reason,
                       properties(context)['distance_meters'] AS distance_meters,
                       properties(context)['time_delta_seconds'] AS time_delta_seconds,
                       geo.name AS location_name, data.filepath AS filepath
                ORDER BY related.time DESC LIMIT $limit
            """, observation_id=observation_id, different_source=different_source,
                limit=self._limit(limit))
            return [self._report_time(_plain(dict(row))) for row in result]

    def search_incidents(self, query: str, limit: int = 10) -> list[dict]:
        limit = self._limit(limit)
        vector_hits = []
        if self.vector is not None:
            try:
                response = self.vector.index("incidents").search(q=query, limit=limit)
                vector_hits = [
                    {"label": hit.get("label"), "neo4j_id": hit.get("neo4j_id") or hit.get("_id"),
                     "score": hit.get("_score"), "text": hit.get("text")}
                    for hit in response.get("hits", [])
                ]
            except Exception:
                vector_hits = []
        with self.graph.session() as session:
            result = session.run("""
                MATCH (i:Incident)
                WHERE toLower(i.label) CONTAINS toLower($search_text)
                OPTIONAL MATCH (r:Report)-[:HAS_LABEL]->(i)
                RETURN i.label AS label, count(DISTINCT r) AS report_count,
                       collect(DISTINCT r.observation_id)[..$limit] AS observation_ids,
                       collect(DISTINCT {observation_id:r.observation_id, source:r.source,
                           time:r.time, summary:r.summary, event_type:r.event_type})[..$limit] AS reports
                ORDER BY report_count DESC LIMIT $limit
            """, search_text=query, limit=limit)
            graph_hits = [_plain(dict(row)) for row in result]
        return [{"vector_matches": vector_hits, "graph_matches": graph_hits}]

    def query_measurements(self, *, source: str, start: str, end: str,
                           sensor: str | None = None, fields: list[str] | None = None,
                           limit: int = 100) -> list[dict]:
        source = self._source(source)
        fields = [self._field(field) for field in (fields or [])]
        with self._cursor() as cursor:
            cursor.execute("""
                SELECT observation_id, observed_at, source, sensor, latitude, longitude, data
                FROM urban_observations
                WHERE source=%s AND observed_at >= %s::timestamptz AND observed_at <= %s::timestamptz
                  AND (%s IS NULL OR sensor=%s)
                ORDER BY observed_at LIMIT %s
            """, (source, start, end, sensor, sensor, self._limit(limit)))
            names = [item.name for item in cursor.description]
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        wanted = set(fields)
        for row in rows:
            if wanted:
                row["data"] = {key: val for key, val in row["data"].items() if key in wanted}
        return [_plain(row) for row in rows]

    def aggregate_measurements(self, *, source: str, field: str, start: str, end: str,
                               sensor: str | None = None, bucket: str = "1 hour",
                               aggregate: str = "avg") -> list[dict]:
        source = self._source(source)
        field = self._field(field)
        if bucket not in self.BUCKETS:
            raise ValueError(f"bucket must be one of: {', '.join(sorted(self.BUCKETS))}")
        aggregate_sql = self.AGGREGATES.get(aggregate)
        if not aggregate_sql:
            raise ValueError(f"aggregate must be one of: {', '.join(sorted(self.AGGREGATES))}")
        # bucket and aggregate are selected exclusively from the whitelists above.
        statement = f"""
            SELECT time_bucket(%s::interval, observed_at) AS bucket,
                   {aggregate_sql}((data ->> %s)::double precision) AS value,
                   count(*) AS samples
            FROM urban_observations
            WHERE source=%s AND observed_at >= %s::timestamptz AND observed_at <= %s::timestamptz
              AND (%s IS NULL OR sensor=%s)
              AND jsonb_typeof(data -> %s) = 'number'
            GROUP BY 1 ORDER BY 1 LIMIT %s
        """
        with self._cursor() as cursor:
            cursor.execute(statement, (bucket, field, source, start, end, sensor, sensor,
                                       field, self.MAX_RESULTS))
            return [_plain({"bucket": row[0], "value": row[1], "samples": row[2]})
                    for row in cursor.fetchall()]

    def close(self):
        self.graph.close()
        self.sql.close()
