#!/usr/bin/env python3
"""
netpipe command line interface.
"""

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from netpipe.audit import start_record
from netpipe.collect import CollectionError, fetch_running_config
from netpipe.deploy import deploy
from netpipe.diff import config_diff, summarise
from netpipe.intent import IntentError, load_intent
from netpipe.inventory import build_nornir
from netpipe.models import off_profile_interfaces
from netpipe.preflight import DeviceReport, preflight_device
from netpipe.render import RenderError, render_site
from netpipe.rollback import rollback
from netpipe.settings import load_settings
from netpipe.verify import snapshot, verify_device


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate an intent file and report what it describes."""
    try:
        intent = load_intent(args.intent)
    except IntentError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    print(f"✓ {args.intent} is valid\n")
    print(f"  Site:    {intent.site} — {intent.description}")
    print(f"  Change:  {intent.change_reference}")
    print(f"  VLANs:   {', '.join(str(v.id) for v in intent.vlans)}\n")

    warnings: list[str] = []

    for device in intent.devices:
        modes: dict[str, int] = {}
        for interface in device.interfaces:
            modes[interface.mode] = modes.get(interface.mode, 0) + 1

        summary = "  ".join(f"{count} {mode}"
                            for mode, count in sorted(modes.items()))
        print(f"  {device.name:<20} {device.model:<12} "
              f"{len(device.interfaces):>3} ports   {summary}")

        for name in off_profile_interfaces(device):
            warnings.append(
                f"{device.name}/{name} is not part of a {device.model} "
                "hardware profile — it will be configured, but check the name"
            )

    if warnings:
        print()
        for warning in warnings:
            print(f"  ⚠ {warning}")

    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Render candidate configs and diff them against the devices."""
    try:
        intent = load_intent(args.intent)
    except IntentError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    try:
        written = render_site(intent)
    except RenderError as exc:
        print(f"✗ render failed: {exc}", file=sys.stderr)
        return 1

    print(f"Rendered {len(written)} candidate configs to artefacts/candidate/\n")

    if args.offline:
        for name, path in written.items():
            lines = len(path.read_text(encoding="utf-8").splitlines())
            print(f"  {name:<20} {lines:>4} lines")
        print("\n(offline — no devices contacted, no diff produced)")
        return 0

    settings = load_settings()
    username = settings.username
    password = settings.password.get_secret_value()

    diff_dir = Path("artefacts/diff")
    diff_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    changes = 0

    for device in intent.devices:
        candidate = written[device.name].read_text(encoding="utf-8")

        try:
            running = fetch_running_config(device, username, password)
        except CollectionError as exc:
            print(f"  ✗ {device.name:<20} {exc}")
            failures += 1
            continue

        diff = config_diff(candidate, running, device.name)

        if not diff:
            print(f"  = {device.name:<20} no textual difference")
            continue

        added, removed = summarise(diff)
        changes += 1
        (diff_dir / f"{device.name}.diff").write_text(
            "\n".join(diff) + "\n", encoding="utf-8")

        print(f"  ~ {device.name:<20} +{added} -{removed} lines")

        if args.show:
            print()
            for line in diff:
                print(f"    {line}")
            print()

    print(f"\n{changes} device(s) would change, {failures} unreachable")
    print("Diffs written to artefacts/diff/ — review before deploying")

    return 1 if failures else 0


def cmd_preflight(args: argparse.Namespace) -> int:
    """Run pre-flight gates against every device in the intent."""
    try:
        intent = load_intent(args.intent)
    except IntentError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    settings = load_settings()

    print(f"Pre-flight for {intent.site} ({len(intent.devices)} devices)\n")

    with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
        reports: list[DeviceReport] = list(pool.map(
            lambda device: preflight_device(device, settings),
            intent.devices,
        ))

    for report in reports:
        mark = "✓" if report.cleared else "✗"
        print(f"{mark} {report.device}")
        for check in report.checks:
            symbol = "✓" if check.passed else "✗"
            print(f"    {symbol} {check.name:<20} {check.detail}")
        print()

    cleared = [r for r in reports if r.cleared]
    blocked = [r for r in reports if not r.cleared]

    print(f"{len(cleared)} cleared, {len(blocked)} blocked")

    if blocked:
        print("\nBlocked devices will be skipped by deploy:")
        for report in blocked:
            reasons = ", ".join(check.name for check in report.failures)
            print(f"  {report.device}: {reasons}")

    return 1 if blocked else 0


def cmd_deploy(args: argparse.Namespace) -> int:
    try:
        intent = load_intent(args.intent)
    except IntentError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    settings = load_settings()

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # 1. Render
    try:
        render_site(intent)
    except RenderError as exc:
        print(f"✗ render failed: {exc}", file=sys.stderr)
        return 1

    # 2. Pre-flight
    print("Running pre-flight...")
    with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
        reports = list(pool.map(
            lambda device: preflight_device(device, settings), intent.devices))

    cleared = [r.device for r in reports if r.cleared]
    blocked = [r for r in reports if not r.cleared]

    for report in blocked:
        reasons = ", ".join(c.name for c in report.failures)
        print(f"  ✗ {report.device} blocked: {reasons}")

    if not cleared:
        print("\n✗ No devices cleared pre-flight. Nothing to do.", file=sys.stderr)
        return 1

    print(f"  ✓ {len(cleared)} device(s) cleared\n")

    # 3. Confirm
    print(f"About to configure {len(cleared)} device(s) "
          f"in batches of {settings.batch_size}:")
    for name in cleared:
        print(f"    {name}")
    print(f"\nChange reference: {intent.change_reference}")

    if args.dry_run:
        print("\n(dry run — nothing was sent)")
        return 0

    if settings.require_approval:
        answer = input("\nType the change reference to proceed: ").strip()
        if answer != intent.change_reference:
            print("✗ Not confirmed. Nothing was sent.", file=sys.stderr)
            return 1

    # 4. Deploy
    nr = build_nornir(intent, settings)
    outcome = deploy(nr, intent, settings, cleared)

    print(f"\n✓ {len(outcome.succeeded)} succeeded")
    if outcome.failed:
        print(f"✗ {len(outcome.failed)} failed")
        for name, reason in outcome.failed.items():
            print(f"    {name}: {reason}")
    if outcome.skipped:
        print(f"– {len(outcome.skipped)} skipped: {', '.join(outcome.skipped)}")

    return 1 if outcome.failed else 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Capture operational state. Run this before deploying."""
    from netpipe.testbed import build_testbed   # imports pyATS — see below

    try:
        intent = load_intent(args.intent)
    except IntentError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    settings = load_settings()
    testbed = build_testbed(intent, settings)

    print(f"Capturing '{args.label}' state for {intent.site}...\n")

    failures = 0
    for device in intent.devices:
        try:
            state = snapshot(testbed, device.name, args.label)
            counts = ", ".join(
                f"{len(value)} {key}" for key, value in state.items())
            print(f"  ✓ {device.name:<20} {counts}")
        except Exception as exc:
            print(f"  ✗ {device.name:<20} {exc}")
            failures += 1

    print(f"\nWritten to artefacts/state/*-{args.label}.json")
    return 1 if failures else 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify applied state against intent and the pre-change snapshot."""
    from netpipe.testbed import build_testbed

    try:
        intent = load_intent(args.intent)
    except IntentError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    settings = load_settings()
    testbed = build_testbed(intent, settings)

    print(f"Verifying {intent.site}...\n")

    reports = []
    for device in intent.devices:
        before_path = Path("artefacts/state") / f"{device.name}-before.json"
        if not before_path.exists():
            print(f"  ⚠ {device.name}: no 'before' snapshot — "
                  "collateral checks skipped")
            before = {}
        else:
            before = json.loads(before_path.read_text(encoding="utf-8"))

        reports.append(verify_device(testbed, device, intent, before))

    errors = 0
    for report in reports:
        mark = "✓" if report.passed else "✗"
        print(f"{mark} {report.device}")

        for finding in report.findings:
            symbol = {"error": "✗", "warning": "⚠", "info": "·"}[finding.severity]
            print(f"    {symbol} {finding.subject:<28} {finding.detail}")
            if finding.severity == "error":
                errors += 1

        if not report.findings:
            print("    · no findings")
        print()

    passed = [r for r in reports if r.passed]
    print(f"{len(passed)}/{len(reports)} device(s) verified, {errors} error(s)")

    return 1 if errors else 0


def cmd_run(args: argparse.Namespace) -> int:
    """The full pipeline: validate, snapshot, preflight, deploy, verify."""
    from netpipe.testbed import build_testbed   # PyATS, as in chapter 6

    # ── Validate ────────────────────────────────────────────────────
    try:
        intent = load_intent(args.intent)
    except IntentError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    settings = load_settings()
    logging.basicConfig(level=settings.log_level,
                        format="%(asctime)s %(levelname)s %(message)s")

    record = start_record(intent, args.intent, dry_run=args.dry_run)
    print(f"═══ {intent.change_reference} — {intent.site} ═══\n")
    print(f"[1/6] validate     ✓ {len(intent.devices)} devices")

    # ── Render ──────────────────────────────────────────────────────
    try:
        written = render_site(intent)
    except RenderError as exc:
        print(f"[2/6] render       ✗ {exc}", file=sys.stderr)
        record.conclude()
        record.write()
        return 1

    record.artefacts["candidate"] = "artefacts/candidate"
    print(f"[2/6] render       ✓ {len(written)} configs")

    # ── Pre-flight ──────────────────────────────────────────────────
    with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
        reports = list(pool.map(
            lambda device: preflight_device(device, settings), intent.devices))

    cleared = [r.device for r in reports if r.cleared]
    for report in reports:
        if not report.cleared:
            record.devices_blocked[report.device] = ", ".join(
                check.name for check in report.failures)

    print(f"[3/6] preflight    {'✓' if cleared else '✗'} "
          f"{len(cleared)} cleared, {len(record.devices_blocked)} blocked")

    for name, reason in record.devices_blocked.items():
        print(f"                     ✗ {name}: {reason}")

    if not cleared:
        record.conclude()
        path = record.write()
        print(f"\n✗ Nothing cleared pre-flight. Record: {path}")
        return 1

    # ── Snapshot ────────────────────────────────────────────────────
    testbed = build_testbed(intent, settings)
    before: dict[str, dict] = {}

    for device in intent.devices:
        if device.name not in cleared:
            continue
        try:
            before[device.name] = snapshot(testbed, device.name, "before")
        except Exception as exc:
            print(f"                     ⚠ {device.name}: "
                  f"no before-snapshot ({exc})")
            before[device.name] = {}

    record.artefacts["state_before"] = "artefacts/state"
    print(f"[4/6] snapshot     ✓ {len(before)} captured")

    # ── Approve and deploy ──────────────────────────────────────────
    if args.dry_run:
        print("[5/6] deploy       – dry run, nothing sent")
        record.conclude()
        path = record.write()
        print(f"\nRecord: {path}")
        return 0

    if settings.require_approval:
        print(f"\nAbout to configure {len(cleared)} device(s).")
        answer = input(f"Type {intent.change_reference} to proceed: ").strip()
        if answer != intent.change_reference:
            print("✗ Not confirmed.", file=sys.stderr)
            record.conclude()
            record.write()
            return 1

    record.approved = True

    nr = build_nornir(intent, settings)
    outcome = deploy(nr, intent, settings, cleared)

    record.devices_configured = outcome.succeeded
    record.devices_failed = outcome.failed
    record.artefacts["backups"] = "artefacts/backup"

    print(f"[5/6] deploy       {'✓' if not outcome.failed else '✗'} "
          f"{len(outcome.succeeded)} configured, {len(outcome.failed)} failed")

    # ── Verify ──────────────────────────────────────────────────────
    to_verify = [d for d in intent.devices if d.name in outcome.succeeded]
    bad: list[str] = []

    for device in to_verify:
        report = verify_device(testbed, device, intent,
                               before.get(device.name, {}))
        record.findings.extend(
            {"device": f.device, "severity": f.severity,
             "subject": f.subject, "detail": f.detail}
            for f in report.findings)

        if report.passed:
            record.devices_verified.append(device.name)
        else:
            bad.append(device.name)

    record.artefacts["state_after"] = "artefacts/state"
    print(f"[6/6] verify       {'✓' if not bad else '✗'} "
          f"{len(record.devices_verified)} verified, {len(bad)} failed")

    for finding in record.findings:
        if finding["severity"] == "error":
            print(f"                     ✗ {finding['device']}/"
                  f"{finding['subject']}: {finding['detail']}")

    # ── Roll back what failed verification ──────────────────────────
    if bad and not args.no_rollback:
        print(f"\n⟲ Rolling back {len(bad)} device(s)...")
        result = rollback(nr, bad)

        record.devices_rolled_back = result.restored
        record.rollback_failures = result.failed

        for name in result.restored:
            print(f"    ✓ {name} restored")
        for name, reason in result.failed.items():
            print(f"    ✗ {name} ROLLBACK FAILED: {reason}")
        for name in result.no_backup:
            print(f"    ✗ {name} no backup available")

    elif bad:
        print(f"\n⚠ {len(bad)} device(s) failed verification. "
              "Rollback suppressed by --no-rollback.")

    # ── Conclude ────────────────────────────────────────────────────
    verdict = record.conclude()
    path = record.write()

    print(f"\n═══ {verdict.upper()} ═══")
    print(f"Record: {path}")

    if record.rollback_failures:
        print("\n‼ DEVICES LEFT IN AN UNINTENDED STATE — ESCALATE",
              file=sys.stderr)
        return 2

    return 0 if verdict in ("success", "dry-run", "no-change") else 1


def build_parser() -> argparse.ArgumentParser:
    # Options every subcommand shares. Defining them on a parent parser
    # means `netpipe show --intent X` works — which is the order people
    # actually type. An argument on the top-level parser would only be
    # accepted *before* the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--intent",
        default="intent/man1.yaml",
        help="path to the site intent file (default: %(default)s)",
    )

    parser = argparse.ArgumentParser(
        prog="netpipe",
        description="Intent-driven configuration pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", parents=[common],
                              help="validate an intent file")
    validate.set_defaults(func=cmd_validate)

    plan = sub.add_parser("plan", parents=[common],
                          help="render configs and diff against devices")
    plan.add_argument("--offline", action="store_true",
                      help="render only; do not contact devices")
    plan.add_argument("--show", action="store_true",
                      help="print the full diff, not just a summary")
    plan.set_defaults(func=cmd_plan)

    preflight = sub.add_parser("preflight", parents=[common],
                               help="run pre-flight gates (read-only)")
    preflight.set_defaults(func=cmd_preflight)

    deploy_cmd = sub.add_parser("deploy", parents=[common],
                                help="apply configuration to devices")
    deploy_cmd.add_argument("--dry-run", action="store_true",
                            help="do everything except send configuration")
    deploy_cmd.set_defaults(func=cmd_deploy)

    snap = sub.add_parser("snapshot", parents=[common],
                          help="capture operational state")
    snap.add_argument("--label", default="before",
                      choices=["before", "after"],
                      help="which snapshot this is (default: %(default)s)")
    snap.set_defaults(func=cmd_snapshot)

    verify_cmd = sub.add_parser("verify", parents=[common],
                                help="verify state against intent")
    verify_cmd.set_defaults(func=cmd_verify)

    run = sub.add_parser("run", parents=[common],
                         help="the full pipeline, end to end")
    run.add_argument("--dry-run", action="store_true",
                     help="stop before deploying")
    run.add_argument("--no-rollback", action="store_true",
                     help="leave failed devices alone (investigate manually)")
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
