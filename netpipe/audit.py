#!/usr/bin/env python3
"""
The change record.
"""

import getpass
import hashlib
import json
import platform
import socket
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from netpipe.models import SiteIntent


def _git_revision() -> str:
    """The commit the pipeline ran from, so the code is identifiable."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _digest(path: Path) -> str:
    """SHA-256 of a file, so the artefact can be shown to be unmodified."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class AuditRecord:
    # Identity
    change_reference: str
    site: str
    started_at: str
    finished_at: str = ""

    # Provenance
    operator: str = field(default_factory=getpass.getuser)
    host: str = field(default_factory=socket.gethostname)
    python_version: str = field(default_factory=platform.python_version)
    git_revision: str = field(default_factory=_git_revision)
    intent_digest: str = ""

    # What happened
    approved: bool = False
    dry_run: bool = False
    devices_targeted: list[str] = field(default_factory=list)
    devices_blocked: dict[str, str] = field(default_factory=dict)
    devices_configured: list[str] = field(default_factory=list)
    devices_failed: dict[str, str] = field(default_factory=dict)
    devices_verified: list[str] = field(default_factory=list)
    devices_rolled_back: list[str] = field(default_factory=list)
    rollback_failures: dict[str, str] = field(default_factory=dict)

    # Evidence
    findings: list[dict] = field(default_factory=list)
    artefacts: dict[str, str] = field(default_factory=dict)

    outcome: str = "incomplete"

    def conclude(self) -> str:
        """Decide the overall verdict."""
        self.finished_at = datetime.now(timezone.utc).isoformat()

        if self.dry_run:
            self.outcome = "dry-run"
        elif self.rollback_failures:
            self.outcome = "rollback-failed"
        elif self.devices_rolled_back:
            self.outcome = "rolled-back"
        elif self.devices_failed:
            self.outcome = "partial-failure"
        elif self.devices_configured and not self.devices_verified:
            self.outcome = "unverified"
        elif self.devices_configured:
            self.outcome = "success"
        else:
            self.outcome = "no-change"

        return self.outcome

    def write(self, directory: str = "artefacts/audit") -> Path:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)

        stamp = self.started_at.replace(":", "").replace("-", "")[:15]
        path = out / f"{self.change_reference}-{stamp}.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

        return path


def start_record(intent: SiteIntent, intent_path: str,
                 dry_run: bool = False) -> AuditRecord:
    return AuditRecord(
        change_reference=intent.change_reference,
        site=intent.site,
        started_at=datetime.now(timezone.utc).isoformat(),
        intent_digest=_digest(Path(intent_path)),
        devices_targeted=[device.name for device in intent.devices],
        dry_run=dry_run,
    )
