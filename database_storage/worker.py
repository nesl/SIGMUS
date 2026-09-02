"""Continuously store complete JSONL records appended by Urban Observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import time

from urban_observation_model import Observation

from .graph import Neo4jStore
from .observation import to_storage_record
from .timescale import TimescaleStore


class StreamWorker:
    def __init__(self, input_path: Path, state_path: Path, poll_seconds: float = 2.0,
                 health_path: Path | None = None, *, sql_store=None, graph=None):
        self.input_path = input_path
        self.state_path = state_path
        self.poll_seconds = poll_seconds
        self.health_path = health_path
        self.sql = sql_store or TimescaleStore()
        self.graph = graph or Neo4jStore()
        self.running = True

    def _offset(self) -> int:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return max(0, int(value.get("offset", 0)))
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
            return 0

    def _save_offset(self, offset: int):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps({"offset": offset}), encoding="utf-8")
        temporary.replace(self.state_path)

    def run_once(self) -> int:
        if not self.input_path.exists():
            return 0
        offset = self._offset()
        if offset > self.input_path.stat().st_size:
            offset = 0
        count = 0
        with self.input_path.open("rb") as stream:
            stream.seek(offset)
            while self.running:
                start = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    stream.seek(start)
                    break
                if not line.strip():
                    self._save_offset(stream.tell())
                    continue
                record = to_storage_record(Observation.from_json(line.decode("utf-8")))
                self.sql.upsert(record)
                self.graph.insert_observation(record)
                self._save_offset(stream.tell())
                count += 1
        return count

    def run(self):
        while self.running:
            try:
                count = self.run_once()
                if self.health_path:
                    self.health_path.parent.mkdir(parents=True, exist_ok=True)
                    self.health_path.touch()
                if count:
                    print(f"Ingested {count} enriched observations", flush=True)
            except Exception as exc:
                print(f"Ingestion retry after error: {exc}", flush=True)
            if self.running:
                time.sleep(self.poll_seconds)

    def stop(self, *_):
        self.running = False

    def close(self):
        self.graph.close()
        self.sql.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Follow an enriched observation JSONL stream")
    parser.add_argument("--input", type=Path, default=Path("/streams/observations.jsonl"))
    parser.add_argument("--state", type=Path, default=Path("/state/ingestion-offset.json"))
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--health", type=Path, default=Path("/state/healthy"))
    args = parser.parse_args(argv)
    worker = StreamWorker(args.input, args.state, args.poll_seconds, args.health)
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    try:
        worker.run()
    finally:
        worker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
