"""SIGMUS storage command-line interface."""

from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Store enriched observations in SIGMUS")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest-stream", help="ingest enriched JSONL once")
    ingest.add_argument("--input", required=True, type=Path)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from .ingestion import ingest_jsonl
    print(f"Ingested {ingest_jsonl(args.input)} observations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
