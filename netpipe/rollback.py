#!/usr/bin/env python3
"""
Restore devices from the backups taken before the change.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command, netmiko_send_config

from netpipe.decorators import audited
from netpipe.diff import normalise

logger = logging.getLogger("netpipe")


class NoBackupError(Exception):
    """There is nothing to roll back to."""


@dataclass
class RollbackOutcome:
    restored: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    no_backup: list[str] = field(default_factory=list)


def latest_backup(device_name: str,
                  directory: str = "artefacts/backup") -> Path:
    """Most recent backup for a device. Timestamps sort lexically."""
    candidates = sorted(Path(directory).glob(f"{device_name}-*.cfg"))
    if not candidates:
        raise NoBackupError(f"no backup found for {device_name}")
    return candidates[-1]


@audited("rollback")
def task_rollback(task: Task) -> Result:
    """
    Restore the pre-change configuration by merging the backup back in.

    This is the simple case and it handles changed values correctly. It
    cannot remove a command the change *added* — see the note below on
    `configure replace` for that.

    Deliberately not retried: a rollback that fails partway needs a human,
    not a replay. It's chapter 5's rule — retry transport, not intent.
    """
    backup = latest_backup(task.host.name)
    config = backup.read_text(encoding="utf-8")

    # The backup is raw `show running-config` output. normalise() strips the
    # "Building configuration..." header, timestamps, "version" and "!" lines.
    lines = [line for line in normalise(config) if line.strip() != "end"]

    task.run(
        task=netmiko_send_config,
        config_commands=lines,
        read_timeout=180,
    )

    task.run(
        task=netmiko_send_command,
        command_string="write memory",
        read_timeout=120,
    )

    return Result(host=task.host,
                  result=f"restored from {backup.name}",
                  changed=True)


def rollback(nr, targets: list[str]) -> RollbackOutcome:
    """Roll back the named devices."""
    outcome = RollbackOutcome()

    havable = []
    for name in targets:
        try:
            latest_backup(name)
            havable.append(name)
        except NoBackupError:
            outcome.no_backup.append(name)
            logger.error("cannot roll back %s: no backup", name)

    if not havable:
        return outcome

    scoped = nr.filter(filter_func=lambda host: host.name in havable)
    # on_failed=True and result[0]: the same Nornir rules as deploy_batch
    results = scoped.run(task=task_rollback, on_failed=True)

    for name, result in results.items():
        if result[0].failed:
            outcome.failed[name] = str(result[0].exception)
            logger.error("rollback FAILED for %s: %s", name, result[0].exception)
        else:
            outcome.restored.append(name)

    return outcome
