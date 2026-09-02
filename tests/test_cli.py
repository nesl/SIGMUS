import json
from pathlib import Path

from kg_construction import cli


def test_available_dates_only_returns_yyyymmdd_directories(tmp_path):
    source = tmp_path / "weather_data"
    (source / "20260814").mkdir(parents=True)
    (source / "20260815").mkdir()
    (source / "metadata").mkdir()
    (source / "20260813.csv").write_text("not a directory", encoding="utf-8")

    assert cli.available_dates(tmp_path, "weather_data") == ["20260814", "20260815"]


def test_validate_config_does_not_contact_services(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    resources = {}
    for name in ("cctv_locations", "owm_locations"):
        path = tmp_path / f"{name}.txt"
        path.write_text("example", encoding="utf-8")
        resources[name] = str(path)
    config = {
        "save_folder": str(data_root),
        "postgres_config": {},
        "neo4j_config": {},
        "openai": {},
        "seaweedfs_config": {},
        "marqo_config": {},
        "vlm_host": {},
        **resources,
    }
    monkeypatch.setattr(cli, "get_config", lambda: config)

    assert cli.validate_config() == []


def test_cli_rejects_missing_source_date(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "get_config", lambda: {"save_folder": str(tmp_path)})

    try:
        cli.run_import("weather", ["20260815"])
    except FileNotFoundError as exc:
        assert "20260815" in str(exc)
    else:
        raise AssertionError("missing input date was accepted")


def test_cli_accepts_enriched_stream_input():
    args = cli.build_parser().parse_args(["ingest-stream", "--input", "records.jsonl"])
    assert args.command == "ingest-stream"
    assert args.input == Path("records.jsonl")
