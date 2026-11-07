import pytest
import yaml
from nornir.core.task import Result

import netpipe.deploy as deploy_module
from netpipe.decorators import retry
from netpipe.deploy import DeployOutcome, deploy_batch
from netpipe.inventory import build_nornir, write_inventory
from netpipe.intent import load_intent
from netpipe.settings import Settings


def test_inventory_matches_intent(tmp_path):
    intent = load_intent("intent/man1.yaml")
    directory = write_inventory(intent, str(tmp_path))

    hosts = yaml.safe_load((directory / "hosts.yaml").read_text())

    assert set(hosts) == {"man1-acc-sw-01", "man1-acc-sw-02"}
    assert hosts["man1-acc-sw-01"]["hostname"] == "10.1.1.11"
    assert hosts["man1-acc-sw-01"]["platform"] == "cisco_ios"
    assert hosts["man1-acc-sw-01"]["data"]["change_reference"] == "CHG0047821"


def test_generated_inventory_holds_no_secrets(tmp_path):
    intent = load_intent("intent/man1.yaml")
    directory = write_inventory(intent, str(tmp_path))

    for name in ("hosts.yaml", "groups.yaml", "defaults.yaml"):
        content = (directory / name).read_text().lower()
        assert "password" not in content
        assert "username" not in content


def test_retry_gives_up_and_reraises():
    calls = []

    @retry(attempts=3, base_delay=0.01)
    def always_fails():
        calls.append(1)
        raise ConnectionError("nope")

    with pytest.raises(ConnectionError):
        always_fails()
    assert len(calls) == 3


def test_retry_stops_once_it_succeeds():
    calls = []

    @retry(attempts=3, base_delay=0.01)
    def fails_once():
        calls.append(1)
        if len(calls) < 2:
            raise ConnectionError("transient")
        return "ok"

    assert fails_once() == "ok"
    assert len(calls) == 2


def test_batching_splits_correctly():
    targets = [f"sw{n:02d}" for n in range(1, 13)]
    size = 5
    batches = [targets[i:i + size] for i in range(0, len(targets), size)]

    assert len(batches) == 3
    assert [len(b) for b in batches] == [5, 5, 2]
    assert [name for batch in batches for name in batch] == targets


def test_recovered_retry_counts_as_success(tmp_path, monkeypatch):
    intent = load_intent("intent/man1.yaml")
    monkeypatch.chdir(tmp_path)               # backups and inventory land here
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    for device in intent.devices:
        (candidate / f"{device.name}.cfg").write_text("hostname x\nend\n")

    calls: dict[str, int] = {}

    def flaky_show(task, command_string, **kwargs):
        calls[task.host.name] = calls.get(task.host.name, 0) + 1
        if command_string == "show running-config" and calls[task.host.name] == 1:
            raise ConnectionError("session dropped")
        return Result(host=task.host, result="hostname x\n")

    def accept_config(task, config_commands, **kwargs):
        return Result(host=task.host, result="ok", changed=True)

    monkeypatch.setattr(deploy_module, "netmiko_send_command", flaky_show)
    monkeypatch.setattr(deploy_module, "netmiko_send_config", accept_config)
    monkeypatch.setattr("netpipe.decorators.time.sleep", lambda seconds: None)

    nr = build_nornir(intent, Settings(username="u", password="p"))
    outcome = DeployOutcome()
    deploy_batch(nr, str(candidate), outcome)

    assert outcome.failed == {}
    assert sorted(outcome.succeeded) == ["man1-acc-sw-01", "man1-acc-sw-02"]
