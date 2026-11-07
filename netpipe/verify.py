#!/usr/bin/env python3
"""
Independent verification of applied changes.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from netpipe.models import Device, SiteIntent

logger = logging.getLogger("netpipe")

# Genie features we care about for an access-layer change
FEATURES = ("interface", "vlan")


@dataclass
class Finding:
    device: str
    severity: str        # "error" | "warning" | "info"
    subject: str
    detail: str


@dataclass
class VerifyReport:
    device: str
    reachable: bool = True
    findings: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.reachable and not any(
            f.severity == "error" for f in self.findings)


def snapshot(testbed, device_name: str, label: str,
             directory: str = "artefacts/state") -> dict:
    """
    Learn operational state and write it to disk.

    `label` is typically 'before' or 'after'.
    """
    device = testbed.devices[device_name]
    device.connect(log_stdout=False, learn_hostname=True)

    state: dict = {}
    try:
        for feature in FEATURES:
            try:
                state[feature] = device.learn(feature).info
            except Exception as exc:      # a feature may be unsupported
                logger.warning("%s: could not learn %s: %s",
                               device_name, feature, exc)
                state[feature] = {}
    finally:
        device.disconnect()

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{device_name}-{label}.json"
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")

    return state


def check_intent_satisfied(device: Device, after: dict) -> list[Finding]:
    """Does operational state match what intent asked for?"""
    findings: list[Finding] = []
    interfaces = after.get("interface", {})

    for wanted in device.interfaces:
        actual = interfaces.get(wanted.name)

        if actual is None:
            findings.append(Finding(
                device.name, "warning", wanted.name,
                "not present in learned state"))
            continue

        # Administrative state
        enabled = actual.get("enabled", False)
        if wanted.enabled and not enabled:
            findings.append(Finding(
                device.name, "error", wanted.name,
                "intent says enabled, device reports shutdown"))
        elif not wanted.enabled and enabled:
            findings.append(Finding(
                device.name, "error", wanted.name,
                "intent says shutdown, device reports enabled"))

        # Access VLAN membership
        if wanted.mode == "access":
            actual_vlan = (actual.get("switchport_access_vlan")
                           or actual.get("access_vlan"))
            if actual_vlan and int(actual_vlan) != wanted.access_vlan:
                findings.append(Finding(
                    device.name, "error", wanted.name,
                    f"intent VLAN {wanted.access_vlan}, "
                    f"device reports VLAN {actual_vlan}"))

    return findings


def check_vlans_exist(device: Device, intent: SiteIntent,
                      after: dict) -> list[Finding]:
    """Every VLAN the site declares should exist on the switch."""
    findings: list[Finding] = []
    learned = after.get("vlan", {}).get("vlans", {})

    for vlan in intent.vlans:
        key = str(vlan.id)
        if key not in learned:
            findings.append(Finding(
                device.name, "error", f"vlan {vlan.id}",
                "declared in intent but not present on the device"))
            continue

        state = learned[key].get("state", "").lower()
        if state and state != "active":
            findings.append(Finding(
                device.name, "warning", f"vlan {vlan.id}",
                f"state is '{state}', expected 'active'"))

    return findings


def check_nothing_else_broke(device_name: str, before: dict,
                             after: dict) -> list[Finding]:
    """
    Did anything that was working before stop working?

    This is the check that catches collateral damage — the interface you
    weren't touching that went down because of a VLAN you removed.
    """
    findings: list[Finding] = []
    was = before.get("interface", {})
    now = after.get("interface", {})

    for name, previous in was.items():
        current = now.get(name)
        if current is None:
            findings.append(Finding(
                device_name, "warning", name,
                "present before the change, absent afterwards"))
            continue

        before_up = previous.get("oper_status") == "up"
        after_up = current.get("oper_status") == "up"

        if before_up and not after_up:
            findings.append(Finding(
                device_name, "error", name,
                f"was up before the change, now "
                f"{current.get('oper_status', 'unknown')}"))

    return findings


def verify_device(testbed, device: Device, intent: SiteIntent,
                  before: dict) -> VerifyReport:
    """Snapshot after the change and run every check."""
    report = VerifyReport(device.name)

    try:
        after = snapshot(testbed, device.name, "after")
    except Exception as exc:
        report.reachable = False
        report.findings.append(Finding(
            device.name, "error", "connection",
            f"could not verify: {exc}"))
        return report

    report.findings.extend(check_intent_satisfied(device, after))
    report.findings.extend(check_vlans_exist(device, intent, after))
    report.findings.extend(check_nothing_else_broke(device.name, before, after))

    return report
