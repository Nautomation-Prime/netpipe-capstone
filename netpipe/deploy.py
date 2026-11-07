#!/usr/bin/env python3
"""
The write phase.
"""

import contextlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from nornir.core.exceptions import NornirSubTaskError
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command, netmiko_send_config

from netpipe.decorators import audited, retry
from netpipe.models import SiteIntent
from netpipe.settings import Settings

logger = logging.getLogger("netpipe")

# A rejected command must fail the task, not scroll past in the output
CISCO_CLI_ERRORS = r"% Invalid|% Incomplete|% Ambiguous"


def discard_connection(task: Task) -> None:
    """Drop the host's cached Netmiko session so a retry opens a fresh one."""
    if "netmiko" in task.host.connections:
        with contextlib.suppress(Exception):   # it may already be dead
            task.host.close_connection("netmiko")


@dataclass
class DeployOutcome:
    succeeded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    backups: dict[str, Path] = field(default_factory=dict)


# ── Nornir tasks ────────────────────────────────────────────────────────

@audited("backup")
@retry(attempts=3, base_delay=2.0)
def task_backup(task: Task) -> Result:
    """Save the running config before touching anything."""
    try:
        reply = task.run(
            task=netmiko_send_command,
            command_string="show running-config",
            read_timeout=120,
        )
    except NornirSubTaskError:
        discard_connection(task)
        raise

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path("artefacts/backup") / f"{task.host.name}-{stamp}.cfg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reply.result, encoding="utf-8")

    return Result(host=task.host, result=str(path))


@audited("configure")
def task_configure(task: Task, candidate_dir: str) -> Result:
    """Apply the candidate configuration."""
    candidate = Path(candidate_dir) / f"{task.host.name}.cfg"
    lines = [
        line for line in candidate.read_text(encoding="utf-8").splitlines()
        if line.strip() and line.strip() not in ("!", "end")
    ]

    reply = task.run(
        task=netmiko_send_config,
        config_commands=lines,
        read_timeout=180,
        error_pattern=CISCO_CLI_ERRORS,
    )

    return Result(host=task.host, result=reply.result, changed=True)


@audited("save")
@retry(attempts=2, base_delay=3.0)
def task_save(task: Task) -> Result:
    """Write the running config to startup."""
    try:
        reply = task.run(
            task=netmiko_send_command,
            command_string="write memory",
            read_timeout=120,
        )
    except NornirSubTaskError:
        discard_connection(task)
        raise
    return Result(host=task.host, result=reply.result)


# ── Orchestration ───────────────────────────────────────────────────────

def deploy_batch(nr, candidate_dir: str, outcome: DeployOutcome) -> None:
    """Back up, configure and save one batch of devices."""
    # Every step runs with on_failed=True: the filters below decide which
    # hosts take part, not Nornir's own failed-hosts list (see the note
    # after this listing). result[0] is each task's own outcome.
    backup = nr.run(task=task_backup, on_failed=True)
    for name, result in backup.items():
        if result[0].failed:
            outcome.failed[name] = f"backup failed: {result[0].exception}"
        else:
            outcome.backups[name] = Path(result[0].result)

    # Never configure a device we could not back up
    safe = nr.filter(filter_func=lambda host: host.name in outcome.backups)
    if not safe.inventory.hosts:
        return

    configured = safe.run(task=task_configure, candidate_dir=candidate_dir,
                          on_failed=True)
    for name, result in configured.items():
        if result[0].failed:
            outcome.failed[name] = f"configure failed: {result[0].exception}"

    applied = safe.filter(
        filter_func=lambda host: host.name not in outcome.failed)
    if not applied.inventory.hosts:
        return

    saved = applied.run(task=task_save, on_failed=True)
    for name, result in saved.items():
        if result[0].failed:
            outcome.failed[name] = f"save failed: {result[0].exception}"
        else:
            outcome.succeeded.append(name)


def deploy(nr, intent: SiteIntent, settings: Settings,
           cleared: list[str], candidate_dir: str = "artefacts/candidate",
           ) -> DeployOutcome:
    """
    Deploy to cleared devices, in batches, stopping if a batch fails.
    """
    outcome = DeployOutcome()

    all_hosts = list(nr.inventory.hosts)
    outcome.skipped = [name for name in all_hosts if name not in cleared]

    targets = [name for name in all_hosts if name in cleared]
    batches = [targets[i:i + settings.batch_size]
               for i in range(0, len(targets), settings.batch_size)]

    for number, batch in enumerate(batches, start=1):
        logger.info("batch %d/%d: %s", number, len(batches), ", ".join(batch))

        scoped = nr.filter(filter_func=lambda host, b=batch: host.name in b)
        deploy_batch(scoped, candidate_dir, outcome)

        if outcome.failed:
            logger.error(
                "batch %d had %d failure(s) — stopping before batch %d",
                number, len(outcome.failed), number + 1,
            )
            remaining = [name for later in batches[number:] for name in later]
            outcome.skipped.extend(remaining)
            break

    return outcome
