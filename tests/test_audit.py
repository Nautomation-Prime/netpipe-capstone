import json

import pytest

from netpipe.audit import AuditRecord, start_record
from netpipe.intent import load_intent
from netpipe.rollback import NoBackupError, latest_backup


@pytest.fixture
def record():
    return start_record(load_intent("intent/man1.yaml"), "intent/man1.yaml")


def test_record_captures_provenance(record):
    assert record.change_reference == "CHG0047821"
    assert record.site == "MAN1"
    assert len(record.intent_digest) == 64      # sha-256 hex
    assert record.operator
    assert record.devices_targeted == ["man1-acc-sw-01", "man1-acc-sw-02"]


def test_digest_changes_with_the_file(tmp_path):
    intent_file = tmp_path / "a.yaml"
    source = load_intent("intent/man1.yaml")

    intent_file.write_text("x", encoding="utf-8")
    first = start_record(source, str(intent_file)).intent_digest

    intent_file.write_text("y", encoding="utf-8")
    second = start_record(source, str(intent_file)).intent_digest

    assert first != second


@pytest.mark.parametrize("setup,expected", [
    (lambda r: setattr(r, "dry_run", True), "dry-run"),
    (lambda r: None, "no-change"),
    (lambda r: r.devices_configured.append("sw1"), "unverified"),
    (lambda r: (r.devices_configured.append("sw1"),
                r.devices_verified.append("sw1")), "success"),
    (lambda r: r.devices_failed.update({"sw1": "boom"}), "partial-failure"),
    (lambda r: r.devices_rolled_back.append("sw1"), "rolled-back"),
    (lambda r: r.rollback_failures.update({"sw1": "unreachable"}),
     "rollback-failed"),
])
def test_outcome_verdicts(record, setup, expected):
    setup(record)
    assert record.conclude() == expected


def test_rollback_failure_outranks_everything(record):
    record.devices_configured.append("sw1")
    record.devices_verified.append("sw1")
    record.devices_rolled_back.append("sw2")
    record.rollback_failures["sw3"] = "unreachable"
    assert record.conclude() == "rollback-failed"


def test_record_writes_valid_json(record, tmp_path):
    record.conclude()
    path = record.write(str(tmp_path))

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["change_reference"] == "CHG0047821"
    assert written["outcome"] == "no-change"


def test_latest_backup_picks_the_newest(tmp_path):
    for stamp in ("20260101T090000Z", "20260920T143107Z", "20260615T120000Z"):
        (tmp_path / f"sw1-{stamp}.cfg").write_text("!", encoding="utf-8")

    assert latest_backup("sw1", str(tmp_path)).name == "sw1-20260920T143107Z.cfg"


def test_no_backup_is_an_explicit_error(tmp_path):
    with pytest.raises(NoBackupError, match="no backup found for sw9"):
        latest_backup("sw9", str(tmp_path))
