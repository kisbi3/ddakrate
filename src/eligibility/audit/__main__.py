from __future__ import annotations

import argparse
from typing import Sequence

from eligibility.audit.replay import replay_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m eligibility.audit")
    subparsers = parser.add_subparsers(dest="command", required=True)
    show = subparsers.add_parser("show", help="Show an audit event sequence")
    show.add_argument("--file", required=True, help="JSONL audit log path")
    show.add_argument("--trace-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "show":
        print(replay_file(args.file, trace_id=args.trace_id))
        return 0
    raise ValueError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
