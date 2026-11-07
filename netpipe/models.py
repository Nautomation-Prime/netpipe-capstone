#!/usr/bin/env python3
"""
The netpipe data contract.

Every rule the pipeline enforces about intent lives in this module.
If it isn't expressed here, it isn't enforced anywhere.
"""

import re
from typing import Annotated, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    IPvAnyAddress,
    IPvAnyInterface,
    field_validator,
    model_validator,
)

# ── Shared constrained types ────────────────────────────────────────────

VlanId = Annotated[int, Field(ge=1, le=4094)]
SiteCode = Annotated[str, Field(pattern=r"^[A-Z]{3}\d$")]

DEVICE_NAME = re.compile(r"^[a-z]{3}\d-(core|dist|acc)-sw-\d{2}$")

# Port counts we know how to expand. Extend as your estate does.
HARDWARE = {
    "C9300-24P": {"access_prefix": "GigabitEthernet1/0/", "access_ports": 24,
                  "uplink_prefix": "TenGigabitEthernet1/1/", "uplink_ports": 4},
    "C9300-48P": {"access_prefix": "GigabitEthernet1/0/", "access_ports": 48,
                  "uplink_prefix": "TenGigabitEthernet1/1/", "uplink_ports": 4},
}


# ── Leaf models ─────────────────────────────────────────────────────────

class Vlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: VlanId
    name: Annotated[str, Field(min_length=1, max_length=32,
                               pattern=r"^[A-Z0-9_-]+$")]
    description: str = ""


class Interface(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=3)]
    description: str = ""
    mode: Literal["access", "trunk", "routed", "unused"]
    access_vlan: Optional[VlanId] = None
    voice_vlan: Optional[VlanId] = None
    trunk_vlans: list[VlanId] = []
    ip_address: Optional[IPvAnyInterface] = None   # address with prefix, e.g. 10.0.0.1/30
    enabled: bool = True

    @model_validator(mode="after")
    def mode_implies_its_own_settings(self):
        name = self.name

        if self.mode == "access":
            if self.access_vlan is None:
                raise ValueError(f"{name}: access ports require access_vlan")
            if self.trunk_vlans:
                raise ValueError(f"{name}: access ports cannot have trunk_vlans")

        if self.mode == "trunk":
            if not self.trunk_vlans:
                raise ValueError(f"{name}: trunk ports require trunk_vlans")
            if self.access_vlan is not None:
                raise ValueError(f"{name}: trunk ports cannot have access_vlan")
            if self.voice_vlan is not None:
                raise ValueError(f"{name}: trunk ports cannot have voice_vlan")

        if self.mode == "routed":
            if self.ip_address is None:
                raise ValueError(f"{name}: routed ports require ip_address")
            if self.access_vlan or self.trunk_vlans or self.voice_vlan:
                raise ValueError(f"{name}: routed ports cannot have switchport VLANs")

        if self.voice_vlan is not None and self.access_vlan == self.voice_vlan:
            raise ValueError(f"{name}: voice_vlan must differ from access_vlan")

        return self


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_name: str
    ntp_servers: Annotated[list[IPvAnyAddress], Field(min_length=1)]
    syslog_server: IPvAnyAddress
    unused_vlan: VlanId
    mtu: Annotated[int, Field(ge=1500, le=9216)] = 1500


# ── Device ──────────────────────────────────────────────────────────────

class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    mgmt_ip: IPvAnyAddress
    platform: Literal["ios", "iosxe", "nxos"]
    role: Literal["core", "distribution", "access", "edge"]
    model: str
    interfaces: list[Interface] = []

    @field_validator("name")
    @classmethod
    def follows_naming_standard(cls, value: str) -> str:
        value = value.strip().lower()
        if not DEVICE_NAME.match(value):
            raise ValueError(
                f"'{value}' does not match the naming standard "
                "<site><n>-<role>-sw-<nn>, e.g. man1-acc-sw-01"
            )
        return value

    @field_validator("model")
    @classmethod
    def model_is_known(cls, value: str) -> str:
        if value not in HARDWARE:
            raise ValueError(
                f"unknown model '{value}'; known models: {sorted(HARDWARE)}"
            )
        return value

    @model_validator(mode="after")
    def interface_names_are_unique(self):
        seen: set[str] = set()
        for interface in self.interfaces:
            if interface.name in seen:
                raise ValueError(f"{self.name}: {interface.name} listed twice")
            seen.add(interface.name)
        return self


# ── Site ────────────────────────────────────────────────────────────────

class SiteIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    site: SiteCode
    description: str = ""
    change_reference: Annotated[str, Field(pattern=r"^CHG\d{7}$")]
    defaults: Defaults
    vlans: Annotated[list[Vlan], Field(min_length=1)]
    devices: Annotated[list[Device], Field(min_length=1)]

    # ── Site-wide rules ─────────────────────────────────────────────

    @model_validator(mode="after")
    def vlan_ids_are_unique(self):
        seen: set[int] = set()
        for vlan in self.vlans:
            if vlan.id in seen:
                raise ValueError(f"VLAN {vlan.id} declared more than once")
            seen.add(vlan.id)
        return self

    @model_validator(mode="after")
    def unused_vlan_is_declared(self):
        declared = {vlan.id for vlan in self.vlans}
        if self.defaults.unused_vlan not in declared:
            raise ValueError(
                f"defaults.unused_vlan is {self.defaults.unused_vlan}, "
                f"which is not in the site VLAN list {sorted(declared)}"
            )
        return self

    @model_validator(mode="after")
    def interfaces_only_use_declared_vlans(self):
        declared = {vlan.id for vlan in self.vlans}

        for device in self.devices:
            for interface in device.interfaces:
                used = set(interface.trunk_vlans)
                if interface.access_vlan:
                    used.add(interface.access_vlan)
                if interface.voice_vlan:
                    used.add(interface.voice_vlan)

                undeclared = used - declared
                if undeclared:
                    raise ValueError(
                        f"{device.name}/{interface.name} uses VLAN(s) "
                        f"{sorted(undeclared)} not declared at site "
                        f"{self.site} (declared: {sorted(declared)})"
                    )
        return self

    @model_validator(mode="after")
    def device_names_match_site(self):
        prefix = self.site.lower()
        for device in self.devices:
            if not device.name.startswith(prefix):
                raise ValueError(
                    f"{device.name} does not belong to site {self.site} "
                    f"(expected the name to start with '{prefix}')"
                )
        return self

    @model_validator(mode="after")
    def management_ips_are_unique(self):
        seen: dict[str, str] = {}
        for device in self.devices:
            key = str(device.mgmt_ip)
            if key in seen:
                raise ValueError(
                    f"{device.name} and {seen[key]} share management IP {key}"
                )
            seen[key] = device.name
        return self


def expand_interfaces(device: Device, unused_vlan: int) -> list[Interface]:
    """
    Return the device's full port inventory.

    Described interfaces are preserved exactly. Every other port the
    hardware has becomes an explicit 'unused' interface. Interfaces named
    in intent but absent from the hardware profile are kept and flagged
    by the caller, never silently dropped.
    """
    profile = HARDWARE[device.model]
    described = {interface.name: interface for interface in device.interfaces}

    expected: list[str] = [
        f"{profile['access_prefix']}{n}"
        for n in range(1, profile["access_ports"] + 1)
    ] + [
        f"{profile['uplink_prefix']}{n}"
        for n in range(1, profile["uplink_ports"] + 1)
    ]

    full: list[Interface] = []
    for name in expected:
        if name in described:
            full.append(described.pop(name))
        else:
            full.append(Interface(
                name=name,
                description="UNUSED",
                mode="unused",
                access_vlan=unused_vlan,
                enabled=False,
            ))

    # Anything left in `described` wasn't in the hardware profile.
    # Keep it — the operator meant something by it — but it's visible.
    full.extend(described.values())
    return full


def off_profile_interfaces(device: Device) -> list[str]:
    """Interface names in intent that the hardware profile doesn't contain."""
    profile = HARDWARE[device.model]
    expected = {
        f"{profile['access_prefix']}{n}"
        for n in range(1, profile["access_ports"] + 1)
    } | {
        f"{profile['uplink_prefix']}{n}"
        for n in range(1, profile["uplink_ports"] + 1)
    }
    return [i.name for i in device.interfaces if i.name not in expected]
