"""MCP server exposing the bounded SIGMUS read API."""

from __future__ import annotations

from functools import lru_cache
import os

from mcp.server.fastmcp import FastMCP

from .query_service import QueryService


mcp = FastMCP(
    "SIGMUS Knowledge Graph",
    instructions="Read-only access to SIGMUS graph and time-series evidence.",
    stateless_http=True,
    json_response=True,
    host=os.environ.get("SIGMUS_QA_HOST", "127.0.0.1"),
    port=int(os.environ.get("SIGMUS_QA_PORT", "8006")),
)


@lru_cache(maxsize=1)
def service() -> QueryService:
    return QueryService()


@mcp.tool()
def search_reports(source: str | None = None, start: str | None = None,
                   end: str | None = None, event_type: str | None = None,
                   text: str | None = None, anomalous: bool | None = None,
                   limit: int = 20) -> list[dict]:
    """Find reports newest-first, optionally filtering Data anomaly status. Latest: no filters, limit=1."""
    return service().search_reports(source=source, start=start, end=end,
                                    event_type=event_type, text=text,
                                    anomalous=anomalous, limit=limit)


@mcp.tool()
def get_report(observation_id: str) -> dict | None:
    """Get graph evidence for one stable observation ID."""
    return service().get_report(observation_id)


@mcp.tool()
def find_related_reports(observation_id: str, limit: int = 20,
                         different_source: bool = False) -> list[dict]:
    """Find related reports with relationship type, evidence, confidence, and report context."""
    return service().find_related_reports(observation_id, limit, different_source)


@mcp.tool()
def search_incidents(query: str, limit: int = 10) -> list[dict]:
    """Search incidents using Marqo semantic candidates and Neo4j labels."""
    return service().search_incidents(query, limit)


@mcp.tool()
def query_measurements(source: str, start: str, end: str, sensor: str | None = None,
                       fields: list[str] | None = None, limit: int = 100) -> list[dict]:
    """Read measurements in an ISO interval with inclusive start and end."""
    return service().query_measurements(source=source, start=start, end=end,
                                        sensor=sensor, fields=fields, limit=limit)


@mcp.tool()
def aggregate_measurements(source: str, field: str, start: str, end: str,
                           sensor: str | None = None, bucket: str = "1 hour",
                           aggregate: str = "avg") -> list[dict]:
    """Aggregate a numeric field; both start and end are inclusive."""
    return service().aggregate_measurements(source=source, field=field, start=start,
                                            end=end, sensor=sensor, bucket=bucket,
                                            aggregate=aggregate)


def main():
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
