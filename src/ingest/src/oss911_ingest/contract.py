"""Source contract loading and validation: the single set of contract rules (DD-88 items fourteen and fifteen).

Deployment (infra/playbooks/45-contracts.yml), M-01 (before writing meta.source_runtime_param) and the ingestion
run itself all use these functions, through `oss911-ingest validate-contract` or by import. No other copy of
these rules may exist: two copies drift, and then a contract passes deployment and fails at run time, or a
hot-tuned value passes M-01 and is rejected by the next deployment.

The database CHECK on meta.source_runtime_param (change 0004) is only a type guard; the rules that depend on
the contract (e.g. "lookback can only be raised") live here.
"""

from __future__ import annotations

import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

CONNECTORS = {"pg_read", "mssql_read", "rest_api", "kafka_consume", "file_drop"}   # CHECK on meta.source_contract
WATERMARK_TYPES = {"timestamptz", "sequence"}                                    # CHECK on meta.watermark_state
IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
RAW_TABLE = re.compile(r"^raw\.[a-z][a-z0-9_]*$")
SECRET_KEY = re.compile(r"passw|secret|private_key", re.IGNORECASE)
LOOKBACK_MAX = 1440          # minutes; same bounds as the database CHECK (0004)


class ContractError(Exception):
    """The contract or the overrides break a rule. args[0] is the list of all problems found."""

    def __init__(self, problems: list[str]):
        super().__init__(problems)
        self.problems = problems

    def __str__(self) -> str:
        return "; ".join(self.problems)


# ---------------------------------------------------------------------------------------------------------------------
# Loading: YAML with duplicate keys is rejected. PyYAML's default loaders silently keep the last value, so a
# contract with two "lookback_minutes" lines would deploy whichever happened to come second.
# ---------------------------------------------------------------------------------------------------------------------
class _StrictLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)            # resolves "<<" merge keys first; those may legitimately repeat keys
    seen: dict[Any, Any] = {}
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r} (first at line {seen[key].start_mark.line + 1})",
                key_node.start_mark)
        seen[key] = key_node
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_contract(text: str) -> dict:
    try:
        doc = yaml.load(text, Loader=_StrictLoader)   # noqa: S506 -- a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise ContractError([f"not valid YAML: {exc}"]) from None
    if not isinstance(doc, dict):
        raise ContractError(["the contract is not a YAML mapping"])
    return doc


# ---------------------------------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------------------------------
def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)   # YAML "true" is a bool, and bool is an int


def _int_in(value: Any, low: int, high: int | None = None) -> bool:
    return _is_int(value) and value >= low and (high is None or value <= high)


def _walk(node: Any, path: str = ""):
    """Yield (path, key, value) for every mapping entry in the document."""
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            yield here, key, value
            yield from _walk(value, here)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{path}[{i}]")


def validate_contract(doc: dict) -> list[str]:
    """All problems found in the contract; an empty list means it is valid."""
    p: list[str] = []

    # Top level: the four fields that are also columns of meta.source_contract, plus enabled
    sid = doc.get("source_id")
    if not (isinstance(sid, str) and IDENT.match(sid)):
        p.append("source_id must be a lower-case identifier")
    if doc.get("connector") not in CONNECTORS:
        p.append(f"connector must be one of {sorted(CONNECTORS)}")
    if not (isinstance(doc.get("target_raw"), str) and RAW_TABLE.match(doc["target_raw"])):
        p.append("target_raw must be raw.<lower-case identifier>")
    if not _int_in(doc.get("contract_version"), 1):
        p.append("contract_version must be a positive integer")
    if not isinstance(doc.get("enabled"), bool):
        p.append("enabled must be true or false")

    # Credentials never in the contract, only references to them (LD-01 2.4)
    for path, key, value in _walk(doc):
        if SECRET_KEY.search(str(key)):
            p.append(f"{path}: looks like a secret; a contract carries credential_ref only (LD-01 2.4)")
        if key in ("credential_ref", "host_ref") and not (isinstance(value, str) and IDENT.match(value)):
            # The reference becomes an environment variable name: OSS911_HOST_<X> / OSS911_CRED_<Y>_USER (LD-01 2.5)
            p.append(f"{path}: must be a lower-case identifier")

    # Where the data comes from and how to log in
    endpoint = doc.get("endpoint")
    if not isinstance(endpoint, dict) or not endpoint:
        p.append("endpoint must be a non-empty mapping")
    auth = doc.get("auth")
    if doc.get("connector") == "rest_api":
        if not isinstance(auth, dict):
            p.append("auth must be a mapping")
        elif "inherits" in auth:
            if not (isinstance(auth["inherits"], str) and IDENT.match(auth["inherits"])):
                p.append("auth.inherits must name another contract's source_id")
        elif not isinstance((auth.get("obtain") or {}).get("credential_ref"), str):
            p.append("auth must have obtain.credential_ref, or inherits another contract's auth")

    # Columns and the idempotency key (LD-01 2.2 · 2.3)
    columns = doc.get("columns")
    src_names: list[str] = []
    if not isinstance(columns, list) or not columns:
        p.append("columns must be a non-empty list")
    else:
        for i, col in enumerate(columns):
            if not (isinstance(col, dict) and isinstance(col.get("src"), str) and col["src"]):
                p.append(f"columns[{i}]: must be a mapping with a non-empty src")
                continue
            if col.get("state") not in ("verified", "mapped_unverified", "unknown"):
                p.append(f"columns[{i}] ({col['src']}): state must be verified, mapped_unverified or unknown")
            src_names.append(col["src"])
        dups = sorted({n for n in src_names if src_names.count(n) > 1})
        if dups:
            p.append(f"columns: src listed more than once: {dups}")
    key = doc.get("idempotent_key")
    if not (isinstance(key, list) and key and all(isinstance(k, str) and k for k in key)):
        p.append("idempotent_key must be a non-empty list of column names")
    elif src_names:
        missing = [k for k in key if k not in src_names]
        if missing:
            p.append(f"idempotent_key refers to columns not in columns: {missing}")

    # Watermark (types as in meta.watermark_state)
    wm = doc.get("watermark")
    if not (isinstance(wm, dict) and wm.get("type") in WATERMARK_TYPES and isinstance(wm.get("column"), str)):
        p.append(f"watermark must have type ({' / '.join(sorted(WATERMARK_TYPES))}) and column")

    # Window and lookback: types and ranges. Only the keys that are present are checked -- sources differ
    # (CDRs have a span and a lookback, day-grain sources a restate period)
    win = doc.get("window")
    if not isinstance(win, dict):
        p.append("window must be a mapping")
    else:
        if "max_span_hours" in win and not _int_in(win["max_span_hours"], 1):
            p.append("window.max_span_hours must be a positive integer")
        if "min_span_seconds" in win and not _int_in(win["min_span_seconds"], 1):
            p.append("window.min_span_seconds must be a positive integer")
        if _int_in(win.get("max_span_hours"), 1) and _int_in(win.get("min_span_seconds"), 1) \
                and win["min_span_seconds"] >= win["max_span_hours"] * 3600:
            p.append("window.min_span_seconds must be shorter than window.max_span_hours")
        if "concurrency" in win and not _int_in(win["concurrency"], 1):
            p.append("window.concurrency must be a positive integer")
        if "lookback_minutes" in win and not _int_in(win["lookback_minutes"], 1, LOOKBACK_MAX):
            # present but null counts as wrong: "lookback_minutes:" with no value is a YAML null
            p.append(f"window.lookback_minutes must be an integer from 1 to {LOOKBACK_MAX}")
        if "restate_days" in win and not _int_in(win["restate_days"], 0):
            p.append("window.restate_days must be a non-negative integer")
        if "schedule" in win and not (isinstance(win["schedule"], str) and win["schedule"].strip()):
            p.append("window.schedule must be a non-empty string")
        if "source_timezone" in win:
            # The zone the source's naive times are in: decides what "now" is for an incremental window
            try:
                ZoneInfo(win["source_timezone"]) if isinstance(win["source_timezone"], str) else ZoneInfo("")
            except (ZoneInfoNotFoundError, ValueError):
                p.append("window.source_timezone must be an IANA time zone name, e.g. UTC or Asia/Riyadh")
        elif "max_span_hours" in win:
            p.append("window.source_timezone is required for a time window (which zone the source's times are in)")

    # Value tables that batch checks compare against must be in the contract: the CSV files in 3rd API/ are
    # development material and are not deployed
    for name, enum in (doc.get("enums") or {}).items():
        if isinstance(enum, dict) and "values" in enum:
            vals = enum["values"]
            if not (isinstance(vals, list) and vals and all(_is_int(v) or isinstance(v, str) for v in vals)
                    and len(set(map(str, vals))) == len(vals)):
                p.append(f"enums.{name}.values must be a non-empty list of distinct codes")
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Hot-tuned values (meta.source_runtime_param) against a contract
# ---------------------------------------------------------------------------------------------------------------------
def validate_overrides(doc: dict, overrides: dict[str, str]) -> list[str]:
    """Problems with the hot-tuned values when laid over this contract.

    Values are text, as stored in meta.source_runtime_param. Each whitelisted parameter overrides one setting
    of the contract and applies only if the contract has that setting.
    """
    p: list[str] = []
    win = doc.get("window") if isinstance(doc.get("window"), dict) else {}
    for name, value in sorted(overrides.items()):
        if name == "lookback_minutes":
            default = win.get("lookback_minutes")
            if not _int_in(default, 1, LOOKBACK_MAX):
                p.append(f"{name}={value}: this contract has no valid window.lookback_minutes to override")
            elif not (isinstance(value, str) and re.fullmatch(r"[0-9]{1,4}", value)) \
                    or not 1 <= int(value) <= LOOKBACK_MAX:
                p.append(f"{name}={value}: must be an integer from 1 to {LOOKBACK_MAX}")
            elif int(value) < default:
                # A shorter lookback only loses data and reports nothing: that is a change of meaning, so it goes
                # through a release that lowers the contract default (DD-88 item fifteen)
                p.append(f"{name}={value}: lookback can only be raised, not below the contract default {default}")
        else:
            p.append(f"{name}={value}: not a hot-tunable parameter")
    return p


def effective_lookback_minutes(doc: dict, overrides: dict[str, str]) -> int:
    """Contract default, raised by a hot-tuned value if there is one. Call only after both have been validated."""
    default = doc["window"]["lookback_minutes"]
    return max(default, int(overrides["lookback_minutes"])) if "lookback_minutes" in overrides else default
