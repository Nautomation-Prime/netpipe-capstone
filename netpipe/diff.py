#!/usr/bin/env python3
"""
Compare candidate configuration against what's running on the device.

This diff is for HUMAN REVIEW. It is deliberately not used to make
automated decisions — see the note in the chapter text.
"""

import difflib
import re

# Lines that change on their own and say nothing about configuration
NOISE = (
    re.compile(r"^Building configuration"),
    re.compile(r"^Current configuration\s*:"),
    re.compile(r"^! Last configuration change"),
    re.compile(r"^! NVRAM config last updated"),
    re.compile(r"^ntp clock-period"),
    re.compile(r"^version \d"),
    re.compile(r"^\s*$"),
)


def normalise(config: str) -> list[str]:
    """Reduce a config to comparable lines."""
    lines: list[str] = []

    for raw in config.splitlines():
        line = raw.rstrip()

        if any(pattern.match(line) for pattern in NOISE):
            continue
        if line.strip() == "!":
            continue

        lines.append(line)

    return lines


def config_diff(candidate: str, running: str, device_name: str) -> list[str]:
    """Return a unified diff of running -> candidate, as a list of lines."""
    return list(difflib.unified_diff(
        normalise(running),
        normalise(candidate),
        fromfile=f"{device_name} (running)",
        tofile=f"{device_name} (candidate)",
        lineterm="",
        n=2,
    ))


def summarise(diff: list[str]) -> tuple[int, int]:
    """Count added and removed lines, ignoring the diff's own headers."""
    added = sum(1 for line in diff
                if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff
                  if line.startswith("-") and not line.startswith("---"))
    return added, removed
