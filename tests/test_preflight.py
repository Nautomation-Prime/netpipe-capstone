from dataclasses import dataclass

import pytest

from netpipe.models import Device
from netpipe.preflight import (
    check_config_register,
    check_free_flash,
    check_identity,
    check_no_active_session,
)


@dataclass
class FakeConnection:
    """Minimal stand-in for a Netmiko connection."""
    prompt: str = "man1-acc-sw-01#"
    responses: dict | None = None

    def find_prompt(self):
        return self.prompt

    def send_command(self, command):
        for fragment, reply in (self.responses or {}).items():
            if fragment in command:
                return reply
        return ""


@pytest.fixture
def device():
    return Device(name="man1-acc-sw-01", mgmt_ip="10.1.1.11",
                  platform="iosxe", role="access", model="C9300-48P")


def test_identity_passes_on_match(device):
    result = check_identity(device, FakeConnection())
    assert result.passed


def test_identity_fails_on_wrong_device(device):
    result = check_identity(device, FakeConnection(prompt="man1-acc-sw-99#"))
    assert not result.passed
    assert "man1-acc-sw-99" in result.detail


def test_identity_ignores_prompt_decoration(device):
    assert check_identity(device, FakeConnection(prompt="man1-acc-sw-01>")).passed
    assert check_identity(device, FakeConnection(prompt="MAN1-ACC-SW-01#")).passed


def test_config_register_flags_bad_value():
    conn = FakeConnection(responses={
        "Configuration register": "Configuration register is 0x2142"})
    result = check_config_register(conn)
    assert not result.passed
    assert "0x2142" in result.detail


def test_config_register_accepts_normal_value():
    conn = FakeConnection(responses={
        "Configuration register": "Configuration register is 0x2102"})
    assert check_config_register(conn).passed


def test_config_register_catalyst_9000_value_is_normal():
    conn = FakeConnection(responses={
        "Configuration register": "Configuration register is 0x102"})
    assert check_config_register(conn).passed


def test_config_register_judges_the_next_reload_value():
    conn = FakeConnection(responses={"Configuration register": (
        "Configuration register is 0x2102 (will be 0x2142 at next reload)")})
    result = check_config_register(conn)
    assert not result.passed
    assert "0x2142" in result.detail


def test_config_register_absent_is_not_a_failure():
    assert check_config_register(FakeConnection()).passed


def test_free_flash_blocks_when_low():
    conn = FakeConnection(responses={
        "dir flash:": "1935477760 bytes total (10485760 bytes free)"})
    result = check_free_flash(conn, minimum_mb=20)
    assert not result.passed
    assert "10 MB free" in result.detail


def test_free_flash_passes_when_ample():
    conn = FakeConnection(responses={
        "dir flash:": "1935477760 bytes total (1490587648 bytes free)"})
    assert check_free_flash(conn, minimum_mb=20).passed


# Captured IOS-XE layout: the Line table, a blank line, then an Interface
# table header that IOS prints even when it has no rows.
SHOW_USERS_TAIL = (
    "\n"
    "  Interface    User               Mode         Idle     Peer Address\n"
)


def test_other_user_blocks():
    conn = FakeConnection(responses={"show users": (
        "    Line       User       Host(s)              Idle       Location\n"
        "   1 vty 0     jsmith     idle                 00:00:12 10.0.0.7\n"
        "*  2 vty 1     admin      idle                 00:00:00 10.0.0.5\n"
        + SHOW_USERS_TAIL
    )})
    result = check_no_active_session(conn, our_username="admin")
    assert not result.passed
    assert "jsmith" in result.detail


def test_only_us_is_fine():
    conn = FakeConnection(responses={"show users": (
        "    Line       User       Host(s)              Idle       Location\n"
        "*  2 vty 1     admin      idle                 00:00:00 10.0.0.5\n"
        + SHOW_USERS_TAIL
    )})
    assert check_no_active_session(conn, our_username="admin").passed
