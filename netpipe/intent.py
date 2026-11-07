#!/usr/bin/env python3
"""
Load raw site intent from YAML.

This is the chapter 1 version: it parses, and that's all. Chapter 2
replaces the return type with validated models.
"""

from pathlib import Path

import yaml


def load_raw_intent(path: str | Path) -> dict:
    """Read a YAML intent file and return it as a plain dictionary."""
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Intent file not found: {path}")

    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if not isinstance(data, dict):
        raise ValueError(f"{path} did not contain a YAML mapping")

    return data
