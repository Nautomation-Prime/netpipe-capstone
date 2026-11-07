import pytest

from netpipe.intent import load_intent
from netpipe.verify import (
    check_intent_satisfied,
    check_nothing_else_broke,
    check_vlans_exist,
)


@pytest.fixture(scope="module")
def intent():
    return load_intent("intent/man1.yaml")


@pytest.fixture
def device(intent):
    return intent.devices[0]


def state_with(**interfaces):
    return {"interface": interfaces}


def test_matching_state_produces_no_findings(device):
    after = state_with(**{
        i.name: {
            "enabled": i.enabled,
            "oper_status": "up" if i.enabled else "down",
            "switchport_access_vlan": str(i.access_vlan or ""),
        }
        for i in device.interfaces
    })
    assert check_intent_satisfied(device, after) == []


def test_wrong_vlan_is_an_error(device):
    after = state_with(**{"GigabitEthernet1/0/1": {
        "enabled": True, "switchport_access_vlan": "1"}})
    findings = check_intent_satisfied(device, after)
    errors = [f for f in findings if f.severity == "error"]
    assert any("VLAN 1" in f.detail for f in errors)


def test_unexpected_shutdown_is_an_error(device):
    after = state_with(**{"GigabitEthernet1/0/1": {
        "enabled": False, "switchport_access_vlan": "10"}})
    findings = check_intent_satisfied(device, after)
    assert any("device reports shutdown" in f.detail for f in findings)


def test_port_that_should_be_shut_but_is_up(device):
    after = state_with(**{"GigabitEthernet1/0/9": {
        "enabled": True, "switchport_access_vlan": "999"}})
    findings = check_intent_satisfied(device, after)
    assert any("device reports enabled" in f.detail for f in findings)


def test_missing_vlan_is_an_error(device, intent):
    after = {"vlan": {"vlans": {"10": {"state": "active"},
                                "20": {"state": "active"}}}}
    findings = check_vlans_exist(device, intent, after)
    subjects = {f.subject for f in findings if f.severity == "error"}
    assert subjects == {"vlan 30", "vlan 999"}


def test_suspended_vlan_is_a_warning(device, intent):
    after = {"vlan": {"vlans": {
        str(v.id): {"state": "active"} for v in intent.vlans}}}
    after["vlan"]["vlans"]["30"]["state"] = "suspended"

    findings = check_vlans_exist(device, intent, after)
    assert [f.severity for f in findings] == ["warning"]


def test_interface_going_down_is_caught():
    before = state_with(**{"TenGigabitEthernet1/1/1": {"oper_status": "up"}})
    after = state_with(**{"TenGigabitEthernet1/1/1": {"oper_status": "down"}})

    findings = check_nothing_else_broke("sw1", before, after)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "was up before" in findings[0].detail


def test_interface_already_down_is_not_flagged():
    before = state_with(**{"GigabitEthernet1/0/9": {"oper_status": "down"}})
    after = state_with(**{"GigabitEthernet1/0/9": {"oper_status": "down"}})
    assert check_nothing_else_broke("sw1", before, after) == []


def test_interface_coming_up_is_not_flagged():
    before = state_with(**{"GigabitEthernet1/0/1": {"oper_status": "down"}})
    after = state_with(**{"GigabitEthernet1/0/1": {"oper_status": "up"}})
    assert check_nothing_else_broke("sw1", before, after) == []
