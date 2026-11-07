#!/usr/bin/env python3
"""
Build a Nornir inventory from validated intent.

The intent file is the source of truth. These files are build output and
are regenerated on every run — never edit them by hand.
"""

from pathlib import Path

import yaml
from nornir import InitNornir
from nornir.core import Nornir

from netpipe.models import SiteIntent
from netpipe.settings import Settings

PLATFORM_DRIVER = {"ios": "cisco_ios", "iosxe": "cisco_ios", "nxos": "cisco_nxos"}


def write_inventory(intent: SiteIntent, directory: str = "inventory") -> Path:
    """Write hosts/groups/defaults from intent. Returns the directory."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)

    hosts = {
        device.name: {
            "hostname": str(device.mgmt_ip),
            "platform": PLATFORM_DRIVER[device.platform],
            "groups": [device.role],
            "data": {
                "site": intent.site,
                "model": device.model,
                "change_reference": intent.change_reference,
            },
        }
        for device in intent.devices
    }

    groups = {
        role: {"data": {"role": role}}
        for role in sorted({device.role for device in intent.devices})
    }

    (out / "hosts.yaml").write_text(
        yaml.safe_dump(hosts, sort_keys=True), encoding="utf-8")
    (out / "groups.yaml").write_text(
        yaml.safe_dump(groups, sort_keys=True), encoding="utf-8")
    (out / "defaults.yaml").write_text(
        yaml.safe_dump({"data": {"site": intent.site}}), encoding="utf-8")

    return out


def build_nornir(intent: SiteIntent, settings: Settings) -> Nornir:
    """Initialise Nornir from generated inventory, with runtime credentials."""
    directory = write_inventory(intent)

    nr = InitNornir(
        runner={
            "plugin": "threaded",
            "options": {"num_workers": settings.max_workers},
        },
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": str(directory / "hosts.yaml"),
                "group_file": str(directory / "groups.yaml"),
                "defaults_file": str(directory / "defaults.yaml"),
            },
        },
        logging={"enabled": False},   # we do our own — see below
    )

    # Credentials are injected at runtime and never written to disk
    nr.inventory.defaults.username = settings.username
    nr.inventory.defaults.password = settings.password.get_secret_value()

    return nr
