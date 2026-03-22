#!/usr/bin/env python3
"""
Runtime config loader for recommendation and evaluation scripts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def load_runtime_config(path: str | None) -> Dict[str, Any]:
    if not path:
        return {}

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    return json.loads(config_path.read_text(encoding="utf-8-sig"))


def merge_value(cli_value: Any, *config_values: Any, default: Any = None) -> Any:
    if cli_value not in (None, ""):
        return cli_value

    for value in config_values:
        if value not in (None, ""):
            return value

    return default


def get_shared_config(config: Dict[str, Any]) -> Dict[str, Any]:
    shared = config.get("shared", {})
    return shared if isinstance(shared, dict) else {}


def get_script_config(config: Dict[str, Any], section_name: str) -> Dict[str, Any]:
    section = config.get(section_name, {})
    return section if isinstance(section, dict) else {}
