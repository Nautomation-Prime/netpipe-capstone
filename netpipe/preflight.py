#!/usr/bin/env python3
"""
Pre-flight gates. Every one of these runs read-only, before any write.
"""

import re
import socket
from dataclasses import dataclass

from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)

from netpipe.models import Device
from netpipe.settings import Settings

PLATFORM_DRIVER = {"ios": "cisco_ios", "iosxe": "cisco_ios", "nxos": "cisco_nxos"}


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class DeviceReport:
    device: str
    checks: list[CheckResult]

    @property
    def cleared(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]


# ── Individual gates ────────────────────────────────────────────────────

def check_reachable(device: Device, timeout: int = 5) -> CheckResult:
    """Is TCP/22 open?"""
    try:
        with socket.create_connection((str(device.mgmt_ip), 22), timeout):
            return CheckResult("reachable", True, f"{device.mgmt_ip}:22 open")
    except OSError as exc:
        return CheckResult("reachable", False, f"{device.mgmt_ip}:22 — {exc}")


def check_identity(device: Device, connection) -> CheckResult:
    """
    Is this the device we think it is?

    The single most important gate. An IP address that has been reassigned,
    a typo in intent, or a DHCP-reused management address all put you on a
    device you did not mean to configure — and every other check will pass
    happily while you do it.
    """
    prompt = connection.find_prompt().rstrip("#>").strip()

    if prompt.lower() != device.name.lower():
        return CheckResult(
            "identity", False,
            f"expected '{device.name}', device says '{prompt}'"
        )
    return CheckResult("identity", True, f"confirmed {prompt}")


# Registers that boot normally and load the startup config. Catalyst 9000
# switches report 0x102; most routers report 0x2102.
NORMAL_CONFIG_REGISTERS = {0x2102, 0x102}


def check_config_register(connection) -> CheckResult:
    """Will this device actually load its configuration on boot?"""
    output = connection.send_command("show version | include Configuration register")
    match = re.search(
        r"Configuration register is (0x[0-9A-Fa-f]+)"
        r"(?:\s*\(will be (0x[0-9A-Fa-f]+) at next reload\))?",
        output,
    )

    if not match:
        return CheckResult("config-register", True, "not reported (non-IOS?)")

    # The next boot is what matters, so prefer the "will be" value if present
    value = (match.group(2) or match.group(1)).lower()
    if int(value, 16) not in NORMAL_CONFIG_REGISTERS:
        return CheckResult(
            "config-register", False,
            f"{value} — device may ignore its startup config"
        )
    return CheckResult("config-register", True, value)


def check_free_flash(connection, minimum_mb: int) -> CheckResult:
    """Is there room to save?"""
    output = connection.send_command("show version | include bytes of memory|free")
    match = re.search(r"(\d+)\s+bytes\s+(?:total\s+)?\(\s*(\d+)\s+bytes free", output)

    if not match:
        output = connection.send_command("dir flash: | include bytes free")
        match = re.search(r"\((\d+)\s+bytes free", output)
        free_mb = int(match.group(1)) // (1024 * 1024) if match else None
    else:
        free_mb = int(match.group(2)) // (1024 * 1024)

    if free_mb is None:
        return CheckResult("free-flash", True, "could not determine (skipped)")

    if free_mb < minimum_mb:
        return CheckResult("free-flash", False,
                           f"{free_mb} MB free, need {minimum_mb} MB")
    return CheckResult("free-flash", True, f"{free_mb} MB free")


def check_no_active_session(connection, our_username: str) -> CheckResult:
    """Is anybody else logged in and configuring?"""
    output = connection.send_command("show users")
    lines = output.splitlines()

    # `show users` is fixed-width, and the Line column is itself several
    # tokens ("2 vty 0"), so splitting on whitespace picks the wrong field.
    # Locate the User column from the header instead.
    header = next((line for line in lines
                   if "User" in line and "Line" in line), None)
    if header is None:
        return CheckResult("no-active-session", True, "could not parse (skipped)")

    start = header.index("User")
    end = header.index("Host(s)") if "Host(s)" in header else start + 20

    others: set[str] = set()
    for line in lines[lines.index(header) + 1:]:
        if not line.strip():
            # End of the Line table. IOS follows it with an "Interface User
            # Mode" table whose columns don't line up with this header.
            break
        user = line[start:end].strip()
        if user and user != our_username:
            others.add(user)

    if others:
        return CheckResult("no-active-session", False,
                           f"also logged in: {', '.join(sorted(others))}")
    return CheckResult("no-active-session", True, "sole session")


# ── Orchestration ───────────────────────────────────────────────────────

def preflight_device(device: Device, settings: Settings) -> DeviceReport:
    """Run every gate against one device. Read-only throughout."""
    checks: list[CheckResult] = []

    reachable = check_reachable(device)
    checks.append(reachable)

    if not reachable.passed:
        # No point attempting the rest
        return DeviceReport(device.name, checks)

    try:
        connection = ConnectHandler(
            device_type=PLATFORM_DRIVER[device.platform],
            host=str(device.mgmt_ip),
            username=settings.username,
            password=settings.password.get_secret_value(),
            secret=(settings.enable_secret.get_secret_value()
                    if settings.enable_secret else ""),
            conn_timeout=settings.connect_timeout,
            fast_cli=False,
        )
    except NetmikoAuthenticationException:
        checks.append(CheckResult("authenticate", False, "credentials rejected"))
        return DeviceReport(device.name, checks)
    except NetmikoTimeoutException as exc:
        checks.append(CheckResult("authenticate", False, f"timed out: {exc}"))
        return DeviceReport(device.name, checks)

    checks.append(CheckResult("authenticate", True, f"as {settings.username}"))

    try:
        checks.append(check_identity(device, connection))
        checks.append(check_config_register(connection))
        checks.append(check_free_flash(connection, settings.min_free_flash_mb))
        checks.append(check_no_active_session(connection, settings.username))
    finally:
        connection.disconnect()

    return DeviceReport(device.name, checks)
