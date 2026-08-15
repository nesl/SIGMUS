"""Command-line interface for importing Urban Observations into SIGMUS."""

from __future__ import annotations

import argparse
from importlib import import_module
from pathlib import Path
import re

from utilities.util import get_config


SOURCE_MODULES = {
    "air": ("air_data", "kg_construction.air_quality_import"),
    "alertcalifornia": ("alertcalifornia", "kg_construction.alertcalifornia_import"),
    "cctv": ("cctv", "kg_construction.caltrans_cctv_import"),
    "citizen": ("citizen_data", "kg_construction.citizen_import"),
    "gdelt": ("gkg", "kg_construction.gdelt.gdelt_events"),
    "pems-incidents": ("pem_data_chp_incidents_day", "kg_construction.pem_incident_import"),
    "pems-stations": ("pem_data_station_5min", "kg_construction.pem_station_import"),
    "twitter": ("twitter_data", "kg_construction.twitter_import"),
    "weather": ("weather_data", "kg_construction.weather_import"),
}


def available_dates(data_root: Path, source_folder: str) -> list[str]:
    folder = data_root / source_folder
    if not folder.is_dir():
        return []
    return sorted(
        path.name
        for path in folder.iterdir()
        if path.is_dir() and re.fullmatch(r"\d{8}", path.name)
    )


def validate_config() -> list[str]:
    config = get_config()
    errors = []
    required_sections = (
        "save_folder",
        "postgres_config",
        "neo4j_config",
        "openai",
        "seaweedfs_config",
        "marqo_config",
        "vlm_host",
    )
    for name in required_sections:
        if name not in config:
            errors.append(f"missing configuration entry: {name}")

    data_root = Path(config.get("save_folder", ""))
    if not data_root.is_dir():
        errors.append(f"save_folder is not a directory: {data_root}")

    for name in ("cctv_locations", "owm_locations"):
        value = config.get(name)
        if not value:
            errors.append(f"missing configuration entry: {name}")
        elif not Path(value).is_file():
            errors.append(f"configured file does not exist: {name}={value}")
    return errors


def run_import(source: str, dates: list[str]) -> None:
    source_folder, module_name = SOURCE_MODULES[source]
    config = get_config()
    data_root = Path(config["save_folder"])
    known_dates = set(available_dates(data_root, source_folder))
    missing = [day for day in dates if day not in known_dates]
    if missing:
        raise FileNotFoundError(
            f"No {source} data directory for: {', '.join(missing)} beneath "
            f"{data_root / source_folder}"
        )

    importer = import_module(module_name)
    importer.pull_by_day_folders(dates)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import Urban Observations into the SIGMUS data stores"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-config", help="validate local paths and required settings")
    subparsers.add_parser("list-sources", help="list supported import source names")

    list_dates = subparsers.add_parser("list-dates", help="list available dates for a source")
    list_dates.add_argument("--source", required=True, choices=sorted(SOURCE_MODULES))

    import_parser = subparsers.add_parser("import", help="import one source into SIGMUS")
    import_parser.add_argument("--source", required=True, choices=sorted(SOURCE_MODULES))
    selection = import_parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--date", action="append", help="YYYYMMDD; repeat for multiple days")
    selection.add_argument("--latest", action="store_true", help="import the newest available day")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "list-sources":
        for source in sorted(SOURCE_MODULES):
            print(source)
        return 0

    if args.command == "validate-config":
        errors = validate_config()
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            return 1
        print("Configuration paths and required settings are valid.")
        return 0

    source_folder, _ = SOURCE_MODULES[args.source]
    data_root = Path(get_config()["save_folder"])
    dates = available_dates(data_root, source_folder)
    if args.command == "list-dates":
        for day in dates:
            print(day)
        return 0

    selected_dates = [dates[-1]] if args.latest and dates else (args.date or [])
    if not selected_dates:
        print(f"ERROR: no dated data directories found for {args.source}")
        return 1
    if any(not re.fullmatch(r"\d{8}", day) for day in selected_dates):
        print("ERROR: every --date must use YYYYMMDD")
        return 1
    run_import(args.source, selected_dates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
