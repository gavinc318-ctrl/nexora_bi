"""Command line entry point: oss911-ingest run --source <contract id> --window <start>/<end>

Exit codes (LD-01 2.5 · DD-84). The DAGs depend on this convention; change both together:
    0  success -- including "the batch was registered as quarantined". Quarantine is a designed outcome,
       not a failure; otherwise Airflow would keep retrying a batch that is bound to be quarantined
    1  run failed (including: another instance for the same source is running and the advisory lock
       could not be taken -- DD-85)
    2  usage or configuration error
The program must never exit with 99: BashOperator treats 99 as "skip" by default, and the task would be
silently marked skipped instead of failed. DAGs always set skip_on_exit_code=None; this module also makes
sure 99 can never come out of it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import __version__

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

WINDOW_FORMAT = "%Y-%m-%dT%H:%M:%S"


class UsageError(Exception):
    """Usage or configuration error; ends with exit code 2."""


def parse_window(text: str) -> tuple[datetime, datetime]:
    """Parse <start>/<end>, e.g. 2026-10-14T00:00:00/2026-10-14T00:05:00.

    Only the format and the order are checked here; the window length limits belong to each source contract
    (e.g. ICP CDRs: at most 24h and at least 5s) and are checked once the contract has been read.
    """
    parts = text.split("/")
    if len(parts) != 2:
        raise UsageError(f"--window must be <start>/<end>, got: {text!r}")
    try:
        start, end = (datetime.strptime(p, WINDOW_FORMAT) for p in parts)
    except ValueError as exc:
        raise UsageError(f"--window times must have the format {WINDOW_FORMAT}: {exc}") from None
    if start >= end:
        raise UsageError(f"--window start must be earlier than its end: {text!r}")
    return start, end


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oss911-ingest",
        description="Fetch by source contract, validate, write raw, advance the watermark (LD-01)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one ingestion for one source and one window")
    run.add_argument("--source", required=True, help="source_id from the contract, e.g. icp_cdr_voice")
    run.add_argument("--window", required=True, help="<start>/<end>, e.g. 2026-10-14T00:00:00/2026-10-14T00:05:00")
    return parser


def cmd_run(args: argparse.Namespace) -> int:
    start, end = parse_window(args.window)
    print(f"oss911-ingest {__version__}: source={args.source} window={start:%Y-%m-%d %H:%M:%S}/{end:%Y-%m-%d %H:%M:%S}")
    print("ingestion logic not implemented yet (batch 2 in progress)", file=sys.stderr)
    return EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)   # on a usage error argparse itself exits with 2
        if args.command == "run":
            return cmd_run(args)
        raise UsageError(f"unknown command: {args.command}")
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except SystemExit:
        raise                            # argparse's 0 (--help / --version) and 2
    except BaseException as exc:         # noqa: BLE001 -- anything unexpected ends with 1, never with 99
        print(f"run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
