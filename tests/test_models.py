import pytest
from pydantic import ValidationError

from netpipe.intent import load_intent
from netpipe.models import Device, Interface, expand_interfaces


def test_real_intent_validates():
    intent = load_intent("intent/man1.yaml")
    assert intent.site == "MAN1"
    assert len(intent.devices) == 2


def test_expansion_covers_every_port():
    intent = load_intent("intent/man1.yaml")
    by_name = {d.name: d for d in intent.devices}
    assert len(by_name["man1-acc-sw-01"].interfaces) == 52   # 48 + 4 uplinks
    assert len(by_name["man1-acc-sw-02"].interfaces) == 28   # 24 + 4 uplinks


def test_described_interfaces_survive_expansion():
    intent = load_intent("intent/man1.yaml")
    device = intent.devices[0]
    gi1 = next(i for i in device.interfaces
               if i.name == "GigabitEthernet1/0/1")
    assert gi1.description == "Desk 101"
    assert gi1.access_vlan == 10
    assert gi1.enabled is True


def test_undescribed_ports_are_shut_and_parked():
    intent = load_intent("intent/man1.yaml")
    device = intent.devices[0]
    gi9 = next(i for i in device.interfaces
               if i.name == "GigabitEthernet1/0/9")
    assert gi9.mode == "unused"
    assert gi9.enabled is False
    assert gi9.access_vlan == intent.defaults.unused_vlan


def test_access_port_without_vlan_rejected():
    with pytest.raises(ValidationError, match="require access_vlan"):
        Interface(name="Gi1/0/1", mode="access")


def test_trunk_with_access_vlan_rejected():
    with pytest.raises(ValidationError, match="cannot have access_vlan"):
        Interface(name="Gi1/0/1", mode="trunk",
                  trunk_vlans=[10], access_vlan=10)


def test_voice_vlan_must_differ():
    with pytest.raises(ValidationError, match="must differ from access_vlan"):
        Interface(name="Gi1/0/1", mode="access",
                  access_vlan=10, voice_vlan=10)


def test_naming_standard_enforced():
    with pytest.raises(ValidationError, match="naming standard"):
        Device(name="switch1", mgmt_ip="10.1.1.1", platform="iosxe",
               role="access", model="C9300-24P")


def test_duplicate_interface_rejected():
    with pytest.raises(ValidationError, match="listed twice"):
        Device(
            name="man1-acc-sw-01", mgmt_ip="10.1.1.1", platform="iosxe",
            role="access", model="C9300-24P",
            interfaces=[
                {"name": "Gi1/0/1", "mode": "access", "access_vlan": 10},
                {"name": "Gi1/0/1", "mode": "access", "access_vlan": 20},
            ],
        )
