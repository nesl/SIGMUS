"""Persistence and graph projection for enriched Urban Observations.

Database clients are loaded lazily so replay/model validation does not require
a running database or every optional database dependency.
"""

__all__ = ["Neo4jStore", "TimescaleStore"]


def __getattr__(name):
    if name == "Neo4jStore":
        from .graph import Neo4jStore
        return Neo4jStore
    if name == "TimescaleStore":
        from .timescale import TimescaleStore
        return TimescaleStore
    raise AttributeError(name)
