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
STALE_DAYS_MAX = 365         # days; same bounds as the database CHECK (0007)
MIDNIGHT_SKIP_MAX = 720      # minutes; same bounds as the database CHECK (0015)
OBJECTS_MAX = 20000          # hindex/agent: at most this many agent IDs per request (interface reference p.935)
INDEX_ID = re.compile(r"^IDX_[0-9]{2}_[0-9]{2}_[0-9]{3}$")
# The agent metrics of hindex/agent (DD-98): which indexes each source's code reads. The sign-in intervals are one
# index whose value is an array; the day totals are one integer per index and must match the contract's columns
SESSION_INDEX = "IDX_04_03_001"
COLUMN_ORIGINS = {"observation"}   # a column not in the response, written by the ingestion program (if03c date_key)
# The synwas datasets (interface reference p.907-933) and the CHECK on raw.icp_reference.dataset (0007)
SNAPSHOT_DATASETS = {"vdns", "agents", "groups", "skills", "skillgroups", "calledconfigs", "restreasons", "servertypes"}

# Required layer (DD-88 item nineteen). The general rules above say what a field must look like when it is
# there; this says which fields a source cannot run without. Declared per (connector, source_id): each source's
# ingestion code reads its own fields (runner.CHECKS registers the batch checks for the same sources), so the
# list is "everything the code for that source reads". A missing field fails validation -- at deployment, in
# M-01, and at the start of a run, which then ends with exit code 2 before touching any data. It must never
# surface as a KeyError in the middle of a run, or as a quarantined batch: a contract defect is not a data defect.
# A source with no entry here has no ingestion code and cannot be released.
REQUIRED = {
    ("rest_api", "icp_cdr_voice"): [
        "endpoint.path", "endpoint.host_ref", "endpoint.request_body",
        "auth.header", "auth.obtain.path", "auth.obtain.credential_ref",
        "window.max_span_hours", "window.min_span_seconds", "window.lookback_minutes", "window.source_timezone",
        "window.schedule",                 # the DAG's schedule, taken into the DAG manifest (LD-01 2.1)
        "watermark.column", "idempotent_key", "columns",
        "enums.call_type.values",          # the batch checks compare CALLTYPE against it (cdr.py)
        "enums.release_cause.classes",     # core's release_source (DD-91 item 5): staging reads it from the contract
    ],
    # Reference data by version-gated snapshots (snapshot.py, DD-94). Logs in with the CDR contract's account
    ("rest_api", "icp_reference"): [
        "endpoint.host_ref", "endpoint.version_probe.path", "endpoint.datasets",
        "endpoint.site_vdn.cc_id", "endpoint.site_vdn.vdn_id",   # the one VDN the dimensions are built from
        "auth.inherits", "window.schedule", "window.version_stale_days", "idempotent_key",
    ],
    # Agent metrics by observation of "today so far" (agent.py, DD-98). Log in with the CDR contract's account
    ("rest_api", "icp_agent_session"): [
        "endpoint.path", "endpoint.host_ref", "endpoint.site_vdn.cc_id", "endpoint.site_vdn.vdn_id",
        "endpoint.index_ids", "endpoint.objects.from", "endpoint.objects.dataset", "endpoint.objects.batch_size",
        "auth.inherits", "window.schedule", "window.day_timezone", "window.source_timezone",
        "idempotent_key", "columns",
    ],
    ("rest_api", "icp_agent_day"): [
        "endpoint.path", "endpoint.host_ref", "endpoint.site_vdn.cc_id", "endpoint.site_vdn.vdn_id",
        "endpoint.index_ids", "endpoint.objects.from", "endpoint.objects.dataset", "endpoint.objects.batch_size",
        "auth.inherits", "window.schedule", "window.day_timezone", "window.midnight_skip_minutes",
        "idempotent_key", "columns",
    ],
}
_MISSING = object()


def _at(doc: Any, path: str) -> Any:
    node = doc
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


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

    # Required layer first: present and not empty (null, "", [] and {} all count as missing)
    required = REQUIRED.get((doc.get("connector"), doc.get("source_id")))
    if required is None:
        p.append(f"no ingestion for connector {doc.get('connector')!r} / source {doc.get('source_id')!r}: "
                 "a contract can only be released together with the code that ingests it")
    else:
        for path in required:
            value = _at(doc, path)
            if value is _MISSING or value is None or value in ("", [], {}):
                p.append(f"{path} is required for {doc.get('source_id')}")

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

    # Columns and the idempotency key (LD-01 2.2 · 2.3). A source of several record shapes (synwas datasets) has no
    # single column list; whether a source needs one is the required layer's business
    columns = doc.get("columns")
    src_names: list[str] = []
    if columns is not None and (not isinstance(columns, list) or not columns):
        p.append("columns must be a non-empty list")
    elif columns is not None:
        for i, col in enumerate(columns):
            if not (isinstance(col, dict) and isinstance(col.get("src"), str) and col["src"]):
                p.append(f"columns[{i}]: must be a mapping with a non-empty src")
                continue
            if col.get("state") not in ("verified", "mapped_unverified", "unknown"):
                p.append(f"columns[{i}] ({col['src']}): state must be verified, mapped_unverified or unknown")
            if "origin" in col and col["origin"] not in COLUMN_ORIGINS:
                p.append(f"columns[{i}] ({col['src']}): origin must be one of {sorted(COLUMN_ORIGINS)}")
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
        if "schedule" in win and not (isinstance(win["schedule"], str) and len(win["schedule"].split()) == 5):
            # Form only: five cron fields. Whether Airflow accepts the expression is decided by Airflow itself --
            # deployment parses the DAGs with the candidate manifest before syncing (45-contracts) -- rather than
            # by a second cron parser here, which would be a new dependency and a second authority
            p.append("window.schedule must be a cron expression of five fields, e.g. '*/5 * * * *'")
        if "version_stale_days" in win and not _int_in(win["version_stale_days"], 1, STALE_DAYS_MAX):
            p.append(f"window.version_stale_days must be an integer from 1 to {STALE_DAYS_MAX}")
        if "midnight_skip_minutes" in win and not _int_in(win["midnight_skip_minutes"], 1, MIDNIGHT_SKIP_MAX):
            p.append(f"window.midnight_skip_minutes must be an integer from 1 to {MIDNIGHT_SKIP_MAX}")
        if "day_timezone" in win:
            # The zone "today" is cut in for an observation of today so far (DD-98 item two)
            try:
                ZoneInfo(win["day_timezone"]) if isinstance(win["day_timezone"], str) else ZoneInfo("")
            except (ZoneInfoNotFoundError, ValueError):
                p.append("window.day_timezone must be an IANA time zone name, e.g. Asia/Riyadh")
        if "source_timezone" in win:
            # The zone the source's naive times are in: decides what "now" is for an incremental window
            try:
                ZoneInfo(win["source_timezone"]) if isinstance(win["source_timezone"], str) else ZoneInfo("")
            except (ZoneInfoNotFoundError, ValueError):
                p.append("window.source_timezone must be an IANA time zone name, e.g. UTC or Asia/Riyadh")
        elif "max_span_hours" in win:
            p.append("window.source_timezone is required for a time window (which zone the source's times are in)")

    # Snapshot datasets (DD-94): each named once, a known dataset, with a path; a VDN-scoped one takes its ccId and
    # vdnId from the version probe, so its path must have both placeholders
    datasets = (endpoint or {}).get("datasets") if isinstance(endpoint, dict) else None
    if datasets is not None:
        if not (isinstance(datasets, list) and datasets):
            p.append("endpoint.datasets must be a non-empty list")
        else:
            names = []
            for i, ds in enumerate(datasets):
                if not isinstance(ds, dict):
                    p.append(f"endpoint.datasets[{i}] must be a mapping")
                    continue
                name = ds.get("name")
                names.append(name)
                if name not in SNAPSHOT_DATASETS:
                    p.append(f"endpoint.datasets[{i}].name must be one of {sorted(SNAPSHOT_DATASETS)}")
                if not (isinstance(ds.get("path"), str) and ds["path"].startswith("/")):
                    p.append(f"endpoint.datasets[{i}] ({name}): path must be an absolute path")
                if ds.get("scope") not in ("cti", "vdn"):
                    p.append(f"endpoint.datasets[{i}] ({name}): scope must be cti or vdn")
                elif ds["scope"] == "vdn" and isinstance(ds.get("path"), str) \
                        and not ("{ccId}" in ds["path"] and "{vdnId}" in ds["path"]):
                    p.append(f"endpoint.datasets[{i}] ({name}): a vdn-scoped path needs {{ccId}} and {{vdnId}}")
                if "key" not in ds or not (ds["key"] is None or (isinstance(ds["key"], str) and ds["key"])):
                    p.append(f"endpoint.datasets[{i}] ({name}): key must be the record's id field, or null if it has none")
            dups = sorted({n for n in names if isinstance(n, str) and names.count(n) > 1})
            if dups:
                p.append(f"endpoint.datasets: listed more than once: {dups}")

    # The site's CTI and VDN (DD-94 item seventeen): compared with the ids of the version probe, which are read as text
    site_vdn = (endpoint or {}).get("site_vdn") if isinstance(endpoint, dict) else None
    if site_vdn is not None:
        if not isinstance(site_vdn, dict):
            p.append("endpoint.site_vdn must be a mapping of cc_id and vdn_id")
        else:
            for leaf in ("cc_id", "vdn_id"):
                if leaf in site_vdn and not (isinstance(site_vdn[leaf], str) and re.fullmatch(r"[0-9]+", site_vdn[leaf])):
                    p.append(f"endpoint.site_vdn.{leaf} must be a quoted string of digits, e.g. \"1\"")

    # Agent metrics (hindex/agent, DD-98): which indexes are asked for, and where the agent IDs come from
    index_ids = (endpoint or {}).get("index_ids") if isinstance(endpoint, dict) else None
    if index_ids is not None:
        if not (isinstance(index_ids, list) and index_ids and all(isinstance(x, str) and INDEX_ID.match(x)
                                                                  for x in index_ids)
                and len(set(index_ids)) == len(index_ids)):
            p.append("endpoint.index_ids must be a non-empty list of distinct index IDs like IDX_04_02_001")
        elif doc.get("source_id") == "icp_agent_session" and index_ids != [SESSION_INDEX]:
            p.append(f"endpoint.index_ids must be [{SESSION_INDEX}] for icp_agent_session (the sign-in intervals)")
        elif doc.get("source_id") == "icp_agent_day":
            idx_cols = [n for n in src_names if n.startswith("IDX_")]
            if sorted(idx_cols) != sorted(index_ids):
                p.append("endpoint.index_ids and the IDX_ columns must be the same set: every index asked for has its "
                         f"column and every column is asked for (only in index_ids: "
                         f"{sorted(set(index_ids) - set(idx_cols))}, only in columns: {sorted(set(idx_cols) - set(index_ids))})")
    objects = (endpoint or {}).get("objects") if isinstance(endpoint, dict) else None
    if objects is not None:
        if not isinstance(objects, dict):
            p.append("endpoint.objects must be a mapping")
        else:
            if "from" in objects and objects["from"] != "icp_reference":
                p.append("endpoint.objects.from must be icp_reference (the agent IDs come from its raw snapshots)")
            if "dataset" in objects and objects["dataset"] != "agents":
                p.append("endpoint.objects.dataset must be agents")
            if "batch_size" in objects and not _int_in(objects["batch_size"], 1, OBJECTS_MAX):
                p.append(f"endpoint.objects.batch_size must be an integer from 1 to {OBJECTS_MAX}")

    # Classification of codes into fixed classes (release causes -> agent / caller / system, DD-91 item 5): every
    # class known, codes integers, no code in two classes
    for name, enum in (doc.get("enums") or {}).items():
        if isinstance(enum, dict) and "classes" in enum:
            cls = enum["classes"]
            allowed = {"release_cause": {"agent", "caller", "system"}}.get(name)
            if not (isinstance(cls, dict) and cls):
                p.append(f"enums.{name}.classes must map a class to a list of codes")
                continue
            if allowed is not None and set(cls) - allowed:
                p.append(f"enums.{name}.classes has unknown classes {sorted(set(cls) - allowed)}; allowed {sorted(allowed)}")
            seen: dict = {}
            for c, codes in cls.items():
                if not (isinstance(codes, list) and codes and all(_is_int(x) for x in codes)):
                    p.append(f"enums.{name}.classes.{c} must be a non-empty list of integer codes")
                    continue
                for x in codes:
                    if x in seen:
                        p.append(f"enums.{name}.classes: code {x} is in both {seen[x]} and {c}")
                    seen[x] = c

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
        elif name == "version_stale_days":
            # Either way is allowed: a longer limit only reports later, it loses no data
            if not _int_in(win.get("version_stale_days"), 1, STALE_DAYS_MAX):
                p.append(f"{name}={value}: this contract has no valid window.version_stale_days to override")
            elif not (isinstance(value, str) and re.fullmatch(r"[0-9]{1,3}", value)) \
                    or not 1 <= int(value) <= STALE_DAYS_MAX:
                p.append(f"{name}={value}: must be an integer from 1 to {STALE_DAYS_MAX}")
        elif name == "midnight_skip_minutes":
            default = win.get("midnight_skip_minutes")
            if not _int_in(default, 1, MIDNIGHT_SKIP_MAX):
                p.append(f"{name}={value}: this contract has no valid window.midnight_skip_minutes to override")
            elif not (isinstance(value, str) and re.fullmatch(r"[0-9]{1,3}", value)) \
                    or not 1 <= int(value) <= MIDNIGHT_SKIP_MAX:
                p.append(f"{name}={value}: must be an integer from 1 to {MIDNIGHT_SKIP_MAX}")
            elif int(value) < default:
                # Shorter could take the previous day's values for the new day's (DD-98 item two): a change of meaning
                p.append(f"{name}={value}: can only be raised, not below the contract default {default}")
        else:
            p.append(f"{name}={value}: not a hot-tunable parameter")
    return p


def effective_lookback_minutes(doc: dict, overrides: dict[str, str]) -> int:
    """Contract default, raised by a hot-tuned value if there is one. Call only after both have been validated."""
    default = doc["window"]["lookback_minutes"]
    return max(default, int(overrides["lookback_minutes"])) if "lookback_minutes" in overrides else default


def effective_stale_days(doc: dict, overrides: dict[str, str]) -> int:
    """Contract default, replaced by a hot-tuned value if there is one. Call only after both have been validated."""
    return int(overrides.get("version_stale_days", doc["window"]["version_stale_days"]))


def effective_midnight_skip_minutes(doc: dict, overrides: dict[str, str]) -> int:
    """Contract default, raised by a hot-tuned value if there is one. Call only after both have been validated."""
    default = doc["window"]["midnight_skip_minutes"]
    return max(default, int(overrides["midnight_skip_minutes"])) if "midnight_skip_minutes" in overrides else default
