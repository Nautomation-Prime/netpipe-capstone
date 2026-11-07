#!/usr/bin/env python3
"""
Load and validate site intent.
"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from netpipe.models import SiteIntent, expand_interfaces


class IntentError(Exception):
    """Intent could not be loaded or did not satisfy the contract."""


def load_intent(path: str | Path, expand: bool = True) -> SiteIntent:
    """
    Read, parse and validate an intent file.

    Raises IntentError with a human-readable report on any failure.
    """
    path = Path(path)

    if not path.exists():
        raise IntentError(f"Intent file not found: {path}")

    try:
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise IntentError(f"{path} is not valid YAML:\n  {exc}") from exc

    if not isinstance(raw, dict):
        raise IntentError(f"{path} did not contain a YAML mapping")

    try:
        intent = SiteIntent.model_validate(raw)
    except ValidationError as exc:
        raise IntentError(format_validation_error(path, exc)) from exc

    if expand:
        for device in intent.devices:
            device.interfaces = expand_interfaces(
                device, intent.defaults.unused_vlan
            )

    return intent


def format_validation_error(path: Path, exc: ValidationError) -> str:
    """Render a ValidationError as something an operator can act on."""
    lines = [f"{path} failed validation ({exc.error_count()} problem(s)):", ""]

    for err in exc.errors():
        location = " -> ".join(str(part) for part in err["loc"])
        message = err["msg"].removeprefix("Value error, ")

        lines.append(f"  {location or 'site-wide'}")
        lines.append(f"      {message}")

        # A site-wide rule failed against the whole document; echoing it
        # back is noise. Only show the input for a specific field.
        if location:
            supplied = repr(err.get("input"))
            if len(supplied) > 80:
                supplied = supplied[:77] + "..."
            lines.append(f"      you supplied: {supplied}")

        lines.append("")

    return "\n".join(lines)
