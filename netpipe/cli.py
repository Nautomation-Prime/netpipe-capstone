#!/usr/bin/env python3
"""
netpipe command line interface.
"""

import argparse
import sys

from netpipe.intent import IntentError, load_intent
from netpipe.models import off_profile_interfaces


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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
