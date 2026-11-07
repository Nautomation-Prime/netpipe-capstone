#!/usr/bin/env python3
"""
Build a PyATS testbed from validated intent.
"""

import copy
from pathlib import Path

import yaml
from genie.testbed import load as load_testbed

from netpipe.models import SiteIntent
from netpipe.settings import Settings

PYATS_OS = {"ios": "iosxe", "iosxe": "iosxe", "nxos": "nxos"}


def build_testbed(intent: SiteIntent, settings: Settings,
                  directory: str = "artefacts"):
    """
    Write a testbed file and return a loaded Genie testbed object.

    Credentials go into the in-memory object only; the file on disk has
    placeholders, exactly as with the Nornir inventory in chapter 5.
    """
    devices = {
        device.name: {
            "os": PYATS_OS[device.platform],
            "type": "switch",
            "credentials": {
                "default": {"username": "%ENV{NETAUTO_USERNAME}",
                            "password": "%ENV{NETAUTO_PASSWORD}"},
            },
            "connections": {
                "cli": {
                    "protocol": "ssh",
                    "ip": str(device.mgmt_ip),
                    "arguments": {"connection_timeout": settings.connect_timeout},
                },
            },
        }
        for device in intent.devices
    }

    document = {
        "testbed": {"name": f"{intent.site}-generated"},
        "devices": devices,
    }

    path = Path(directory) / "testbed.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    # The loaded object gets the real values, from the same Settings that
    # pre-flight used, so a .env file works here exactly as it does there.
    in_memory = copy.deepcopy(document)
    for spec in in_memory["devices"].values():
        spec["credentials"]["default"] = {
            "username": settings.username,
            "password": settings.password.get_secret_value(),
        }

    return load_testbed(in_memory)
