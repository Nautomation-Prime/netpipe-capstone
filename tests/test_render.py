import pytest
from jinja2 import UndefinedError

from netpipe.intent import load_intent
from netpipe.models import Interface
from netpipe.render import build_environment, render_device
from netpipe.diff import normalise, config_diff, summarise


@pytest.fixture(scope="module")
def intent():
    return load_intent("intent/man1.yaml")


def test_renders_without_error(intent):
    config = render_device(intent, intent.devices[0])
    assert config.startswith("hostname man1-acc-sw-01")
    assert config.rstrip().endswith("end")


def test_described_port_gets_its_vlan(intent):
    config = render_device(intent, intent.devices[0])
    assert "interface GigabitEthernet1/0/1" in config
    assert " switchport access vlan 10" in config
    assert " switchport voice vlan 20" in config


def test_unused_port_is_parked_and_shut(intent):
    config = render_device(intent, intent.devices[0])
    block = config.split("interface GigabitEthernet1/0/9\n")[1].split("!")[0]
    assert "switchport access vlan 999" in block
    assert "shutdown" in block
    assert "no shutdown" not in block


def test_trunk_vlans_are_comma_joined(intent):
    config = render_device(intent, intent.devices[0])
    assert " switchport trunk allowed vlan 10,20,30" in config


def test_every_physical_port_appears(intent):
    config = render_device(intent, intent.devices[0])
    assert config.count("\ninterface ") == 52


def test_routed_port_uses_its_own_prefix(intent):
    routed = Interface(name="GigabitEthernet1/0/48", mode="routed",
                       ip_address="10.0.0.1/30")
    device = intent.devices[0].model_copy(update={"interfaces": [routed]})
    config = render_device(intent, device)
    assert " ip address 10.0.0.1 255.255.255.252" in config


def test_strict_undefined_catches_template_typos(intent, tmp_path):
    (tmp_path / "access_switch.j2").write_text(
        "hostname {{ device.nmae }}\n", encoding="utf-8")
    env = build_environment(str(tmp_path))

    with pytest.raises(Exception) as caught:
        render_device(intent, intent.devices[0], env)
    assert "nmae" in str(caught.value)


def test_normalise_strips_noise():
    raw = (
        "Building configuration...\n"
        "Current configuration : 4231 bytes\n"
        "!\n"
        "! Last configuration change at 09:14:02 UTC Mon Sep 14 2026\n"
        "version 17.9\n"
        "hostname man1-acc-sw-01\n"
    )
    assert normalise(raw) == ["hostname man1-acc-sw-01"]


def test_identical_configs_produce_no_diff():
    config = "hostname sw1\n!\ninterface Gi1/0/1\n shutdown\n"
    assert config_diff(config, config, "sw1") == []


def test_summarise_ignores_headers():
    diff = config_diff("hostname sw2\n", "hostname sw1\n", "sw1")
    added, removed = summarise(diff)
    assert (added, removed) == (1, 1)
