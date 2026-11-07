#!/usr/bin/env python3
"""
Render candidate configurations from validated intent.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from netpipe.models import Device, SiteIntent


class RenderError(Exception):
    """A template failed to render."""


def build_environment(template_dir: str = "templates") -> Environment:
    return Environment(
        loader=FileSystemLoader(template_dir),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )


def render_device(intent: SiteIntent, device: Device,
                  env: Environment | None = None) -> str:
    """Render one device's candidate configuration."""
    env = env or build_environment()

    try:
        template = env.get_template("access_switch.j2")
        return template.render(
            device=device,
            site=intent.site,
            defaults=intent.defaults,
            vlans=intent.vlans,
        )
    except TemplateError as exc:
        raise RenderError(f"{device.name}: {exc}") from exc


def render_site(intent: SiteIntent,
                output_dir: str = "artefacts/candidate") -> dict[str, Path]:
    """
    Render every device at the site.

    Returns a mapping of device name to the file the config was written to.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    env = build_environment()
    written: dict[str, Path] = {}

    for device in intent.devices:
        config = render_device(intent, device, env)
        path = out / f"{device.name}.cfg"
        path.write_text(config, encoding="utf-8")
        written[device.name] = path

    return written
