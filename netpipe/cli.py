#!/usr/bin/env python3
"""
netpipe command line interface.
"""

import argparse
import sys

from netpipe.intent import load_raw_intent


def cmd_show(args: argparse.Namespace) -> int:
    """Print a summary of an intent file."""
    try:
        intent = load_raw_intent(args.intent)
    except (FileNotFoundError, ValueError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    print(f"Site:    {intent.get('site')}")
    print(f"Change:  {intent.get('change_reference')}")
    print(f"VLANs:   {len(intent.get('vlans', []))}")
    print(f"Devices: {len(intent.get('devices', []))}\n")

    for device in intent.get("devices", []):
        described = len(device.get("interfaces", []))
        print(f"  {device['name']:<20} {device['mgmt_ip']:<12} "
              f"{device.get('model', '?'):<12} {described} interfaces described")

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

    show = sub.add_parser("show", parents=[common],
                          help="summarise an intent file")
    show.set_defaults(func=cmd_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
