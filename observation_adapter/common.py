"""Read shared enrichment without invoking observation-level models or geocoders."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from urban_observation_model import Observation


def to_sigmus_record(observation: Observation) -> dict:
    value = observation.value
    annotations = value.get("annotations") or {}
    enrichment = annotations.get("enrichment") or {}
    status = enrichment.get("status")
    if status not in {"completed", "skipped_by_anomaly"}:
        raise ValueError(
            f"observation {observation.id} has no authoritative shared enrichment; "
            "send it through the Urban Observations enrichment service first"
        )
    location = annotations.get("location") if isinstance(annotations.get("location"), dict) else {}
    latitude = location.get("latitude", value.get("latitude"))
    longitude = location.get("longitude", value.get("longitude"))
    return {
        "id": observation.id,
        "source": value["source"],
        "time": value["time"],
        "end_time": value.get("end_time"),
        "sensor": value["sensor"],
        "latitude": latitude,
        "longitude": longitude,
        "data": dict(value["data"]),
        "files": observation.files,
        "event": dict(annotations.get("event") or {}),
        "summary": annotations.get("summary"),
        "entities": list(annotations.get("entities") or []),
        "relations": list(annotations.get("relations") or []),
        "effects": list(annotations.get("effects") or []),
        "incidents": list(annotations.get("incidents") or []),
        "anomaly": dict(annotations.get("anomaly") or {}),
        "enrichment": dict(enrichment),
    }


def iter_jsonl(path: str | Path) -> Iterator[dict]:
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield to_sigmus_record(Observation.from_json(line))
