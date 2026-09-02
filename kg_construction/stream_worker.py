"""Continuously ingest complete JSONL records appended by Urban Observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import time

from urban_observation_model import Observation

from kg_construction.db_manager import KGManager
from kg_construction.stream_ingestion import ObservationStore
from observation_adapter import to_sigmus_record


class StreamWorker:
    def __init__(self, input_path: Path, state_path: Path, poll_seconds: float = 2.0,
                 health_path: Path | None = None, *, sql_store=None, graph=None):
        self.input_path = input_path
        self.state_path = state_path
        self.poll_seconds = poll_seconds
        self.health_path = health_path
        self.sql = sql_store or ObservationStore()
        self.graph = graph or KGManager(use_vectordb=True, enable_graph_reasoning=True)
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
        size = self.input_path.stat().st_size
        if offset > size:  # The producer rotated or truncated the output file.
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
                    # Do not consume a partially appended JSON object.
                    stream.seek(start)
                    break
                if not line.strip():
                    self._save_offset(stream.tell())
                    continue
                observation = Observation.from_json(line.decode("utf-8"))
                record = to_sigmus_record(observation)
                self.sql.upsert(record)
                self.graph.insert_common_observation(record)
                self._save_offset(stream.tell())
                count += 1
        return count

    def run(self):
        while self.running:
            try:
                count = self.run_once()
                if self.health_path is not None:
                    self.health_path.parent.mkdir(parents=True, exist_ok=True)
                    self.health_path.touch()
                if count:
                    print(f"Ingested {count} enriched observations", flush=True)
            except Exception as exc:
                # The offset advances only after both database projections, so
                # an interrupted record is safely retried via idempotent writes.
                print(f"Ingestion retry after error: {exc}", flush=True)
            if self.running:
                time.sleep(self.poll_seconds)

    def stop(self, *_):
        self.running = False

    def close(self):
        self.graph.close_driver()
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
