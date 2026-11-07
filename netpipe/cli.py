#!/usr/bin/env python3
"""
netpipe command line interface.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from netpipe.collect import CollectionError, fetch_running_config
from netpipe.diff import config_diff, summarise
from netpipe.intent import IntentError, load_intent
from netpipe.models import off_profile_interfaces
from netpipe.preflight import DeviceReport, preflight_device
from netpipe.render import RenderError, render_site
from netpipe.settings import load_settings


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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
