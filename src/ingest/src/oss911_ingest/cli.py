"""Command line entry point.

    oss911-ingest run --source <contract id> [--start-at <time>]                     incremental (DAG)
    oss911-ingest run --source <contract id> --mode backfill --window <start>/<end>  manual backfill
    oss911-ingest run --source <contract id> --mode resweep [--days N]               daily re-sweep (DAG)
    oss911-ingest dag-manifest --pools <pools.yml> --out <dags.json> [--candidate <contracts dir>]
    oss911-ingest validate-contract <contract.yaml> [--overrides <json> | --with-overrides [--source <id>]]

Exit codes (LD-01 2.5 · DD-84). The DAGs depend on this convention; change both together:
    0  success -- including "the batch was registered as quarantined". Quarantine is a designed outcome,
       not a failure; otherwise Airflow would keep retrying a batch that is bound to be quarantined.
       For validate-contract: the contract (and the overrides) are valid
    1  run failed (including: another instance for the same source is running and the advisory lock
       could not be taken -- DD-85). For validate-contract: the contract or the overrides break a rule
    2  usage or configuration error
The program must never exit with 99: BashOperator treats 99 as "skip" by default, and the task would be
silently marked skipped instead of failed. DAGs always set skip_on_exit_code=None; this module also makes
sure 99 can never come out of it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from . import __version__
from .config import ConfigError, db_settings
from .contract import ContractError, load_contract, validate_contract, validate_overrides

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

    run = sub.add_parser(
        "run", help="run one ingestion for one source",
        description="incremental (default): from the watermark minus the lookback up to now, advancing the watermark. "
                    "backfill: an explicit window, the watermark untouched (DD-89). Times are in the source's own "
                    "time zone (contract window.source_timezone).")
    run.add_argument("--source", required=True, help="source_id from the contract, e.g. icp_cdr_voice")
    run.add_argument("--mode", choices=["incremental", "backfill", "resweep"], default="incremental")
    run.add_argument("--days", type=int, default=1,
                     help="resweep only: how many whole days back from today's midnight (source time zone); default 1")
    run.add_argument("--window", help="backfill only: <start>/<end>, e.g. 2026-10-14T00:00:00/2026-10-15T00:00:00")
    run.add_argument("--start-at", metavar="TIME",
                     help="incremental, first run of a source only (no watermark yet): where the watermark starts, "
                          "e.g. 2026-10-20T00:00:00")

    val = sub.add_parser(
        "validate-contract",
        help="check a contract file, optionally together with hot-tuned values (DD-88)",
        description="The single set of contract rules, used by deployment and by M-01. On success prints one JSON "
                    "line: source_id, contract_version and the overrides that were checked.")
    val.add_argument("contract", help="path of the contract YAML file")
    group = val.add_mutually_exclusive_group()
    group.add_argument("--overrides", metavar="JSON",
                       help='hot-tuned values to check, as a JSON object of text values, e.g. \'{"lookback_minutes": "30"}\' '
                            "(M-01 checks a value before writing it)")
    group.add_argument("--with-overrides", action="store_true",
                       help="check the values currently in meta.source_runtime_param for this source (deployment)")
    val.add_argument("--source", help="with --with-overrides: must equal the contract's source_id (a guard against "
                                      "checking one source's file with another source's values); defaults to it")
    man = sub.add_parser(
        "dag-manifest", help="write the DAG manifest from the contracts in the database (LD-01 2.1)",
        description="Lists every source managed in meta.source_contract (disabled ones included) with its schedule, "
                    "pool and contract version. Written atomically and only when changed, under the contract-sync "
                    "advisory lock. With --candidate, the released contracts in that directory are laid over the "
                    "database first -- the state after the coming sync -- for parsing the DAGs before syncing.")
    man.add_argument("--pools", required=True, help="contracts/pools.yml: source_id -> Airflow pool")
    man.add_argument("--out", required=True, help="the manifest to write, e.g. /opt/oss911/release/dags.json")
    man.add_argument("--candidate", metavar="DIR", help="a contracts directory (release.yml and the released files)")
    return parser


def _load_mapping(path: str, what: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return load_contract(fh.read())        # the strict loader: duplicate keys are refused here as well
    except OSError as exc:
        raise UsageError(f"cannot read {what} {path}: {exc.strerror}") from None


def cmd_dag_manifest(args: argparse.Namespace) -> int:
    from . import manifest

    try:
        pools_doc = _load_mapping(args.pools, "pool mapping")
        pools = pools_doc.get("pools")
        if not (isinstance(pools, dict) and all(isinstance(k, str) and isinstance(v, str) and v for k, v in pools.items())):
            print(f"{args.pools}: 'pools' must map source_id to a pool name", file=sys.stderr)
            return EXIT_FAILED
        candidates = {}
        if args.candidate:
            release = _load_mapping(os.path.join(args.candidate, "release.yml"), "release list").get("release") or []
            for name in release:
                doc = _load_mapping(os.path.join(args.candidate, os.path.basename(name)), "contract")
                candidates[doc["source_id"]] = doc
    except ContractError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_FAILED

    settings = db_settings()
    import psycopg

    with psycopg.connect(**settings.connect_kwargs()) as conn:
        if not args.candidate:
            # Same lock as the contract sync, held from reading the database to the rename (module docstring)
            conn.execute("SET lock_timeout = '120s'")
            conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (manifest.LOCK_KEY,))
        try:
            managed = {sid: {"contract_version": ver, "enabled": enabled, "definition": definition}
                       for sid, ver, enabled, definition in conn.execute(
                           "SELECT source_id, contract_version, enabled, definition FROM meta.source_contract")}
            for sid, doc in candidates.items():
                managed[sid] = {"contract_version": doc["contract_version"],
                                "enabled": managed.get(sid, {}).get("enabled", doc.get("enabled")),
                                "definition": {k: v for k, v in doc.items() if k != "enabled"}}
            try:
                data = manifest.serialize(manifest.build(managed, pools))
            except manifest.ManifestError as exc:
                for line in exc.problems:
                    print(f"manifest: {line}", file=sys.stderr)
                return EXIT_FAILED
            try:
                changed = manifest.write_atomic(args.out, data)
            except OSError as exc:
                raise UsageError(f"cannot write {args.out}: {exc.strerror}") from None
        finally:
            if not args.candidate and not conn.closed:
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (manifest.LOCK_KEY,))
    print(json.dumps({"out": args.out, "changed": changed, "sources": sorted(managed),
                      "candidate": bool(args.candidate)}, sort_keys=True))
    return EXIT_OK


def read_overrides_from_db(source_id: str) -> dict[str, str]:
    settings = db_settings()   # configuration first: a missing file is a configuration error (2), whatever else is wrong
    import psycopg             # imported here: validating a file alone must not need the driver or a database

    with psycopg.connect(**settings.connect_kwargs()) as conn:
        rows = conn.execute(
            "SELECT param_name, param_value FROM meta.source_runtime_param WHERE source_id = %s",
            (source_id,)).fetchall()
    return dict(rows)


def cmd_validate_contract(args: argparse.Namespace) -> int:
    if args.source and not args.with_overrides:
        raise UsageError("--source is only used with --with-overrides")
    try:
        with open(args.contract, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise UsageError(f"cannot read {args.contract}: {exc.strerror}") from None

    try:
        doc = load_contract(text)
    except ContractError as exc:
        print(f"{args.contract}: {exc}", file=sys.stderr)
        return EXIT_FAILED
    problems = validate_contract(doc)

    overrides: dict[str, str] = {}
    if args.overrides is not None:
        try:
            overrides = json.loads(args.overrides)
        except json.JSONDecodeError as exc:
            raise UsageError(f"--overrides is not valid JSON: {exc}") from None
        if not (isinstance(overrides, dict) and all(isinstance(v, str) for v in overrides.values())):
            raise UsageError("--overrides must be a JSON object whose values are strings, as stored in the database")
    elif args.with_overrides:
        if args.source and args.source != doc.get("source_id"):
            problems.append(f"--source {args.source} does not match the contract's source_id {doc.get('source_id')}")
        elif not problems:      # an invalid contract may have no usable source_id; its problems are reported first
            overrides = read_overrides_from_db(doc["source_id"])
    problems += validate_overrides(doc, overrides)

    if problems:
        for line in problems:
            print(f"{args.contract}: {line}", file=sys.stderr)
        return EXIT_FAILED
    print(json.dumps({"source_id": doc["source_id"], "contract_version": doc["contract_version"],
                      "overrides": overrides}, sort_keys=True))
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    from .runner import RunFailed, Runner

    window = start_at = None
    if args.mode != "resweep" and args.days != 1:
        raise UsageError("--days is for --mode resweep")
    if args.mode == "resweep":
        if args.window is not None or args.start_at is not None:
            raise UsageError("--mode resweep computes its own window; it takes no --window or --start-at")
        if not 1 <= args.days <= 30:
            raise UsageError("--days must be from 1 to 30")
    elif args.mode == "backfill":
        if args.window is None or args.start_at is not None:
            raise UsageError("--mode backfill needs --window and takes no --start-at")
        window = parse_window(args.window)
    else:
        if args.window is not None:
            raise UsageError("--window is for --mode backfill; incremental runs start from the watermark")
        if args.start_at is not None:
            try:
                start_at = datetime.strptime(args.start_at, WINDOW_FORMAT)
            except ValueError as exc:
                raise UsageError(f"--start-at must have the format {WINDOW_FORMAT}: {exc}") from None
    try:
        return Runner(args.source, args.mode, window, start_at, resweep_days=args.days).run()
    except RunFailed as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)   # on a usage error argparse itself exits with 2
        if args.command == "run":
            return cmd_run(args)
        if args.command == "validate-contract":
            return cmd_validate_contract(args)
        if args.command == "dag-manifest":
            return cmd_dag_manifest(args)
        raise UsageError(f"unknown command: {args.command}")
    except (UsageError, ConfigError) as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except SystemExit:
        raise                            # argparse's 0 (--help / --version) and 2
    except BaseException as exc:         # noqa: BLE001 -- anything unexpected ends with 1, never with 99
        print(f"run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
