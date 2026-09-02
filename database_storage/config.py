"""Configuration shared by SIGMUS storage and Q/A services."""

from __future__ import annotations

import json
import os
from pathlib import Path


def get_config(path: str | Path | None = None) -> dict:
    """Read the single JSON configuration file used by host and containers."""
    resolved = Path(path or os.environ.get("URBAN_SYSTEM_CONFIG", "config.json"))
    with resolved.open(encoding="utf-8") as stream:
        return json.load(stream)
