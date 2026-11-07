#!/usr/bin/env python3
"""
Read-only collection from devices.
"""

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from netpipe.models import Device

PLATFORM_DRIVER = {
    "ios": "cisco_ios",
    "iosxe": "cisco_ios",
    "nxos": "cisco_nxos",
}


class CollectionError(Exception):
    """Could not read from a device."""


def fetch_running_config(device: Device, username: str, password: str) -> str:
    """Retrieve the running configuration. Read-only."""
    try:
        with ConnectHandler(
            device_type=PLATFORM_DRIVER[device.platform],
            host=str(device.mgmt_ip),
            username=username,
            password=password,
            conn_timeout=20,
            fast_cli=False,
        ) as connection:
            return connection.send_command("show running-config")

    except NetmikoAuthenticationException as exc:
        raise CollectionError(f"{device.name}: authentication failed") from exc
    except NetmikoTimeoutException as exc:
        raise CollectionError(f"{device.name}: unreachable at {device.mgmt_ip}") from exc
