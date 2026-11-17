"""Print metrics/metrics.yaml and metrics/marts.yaml as one JSON object, for metric_sync.sql (DD-97 · DD-99 ·
LD-03 chapter 5).

Usage:  <venv-ingest>/bin/python metrics_to_json.py <metrics.yaml> <marts.yaml> <dbt_project.yml>

46-metrics.yml runs this on the target, over the release directory's copy, with venv-ingest's PyYAML. It checks
what the database cannot see from one row -- every field present and typed, codes unique, the mart mapping complete
for its aggregation -- and computes definition_sha256 over the fields that define a metric (LD-03 chapter 6: a
change to any of them is a change of definition). Whether the mart columns exist is checked in the database.

A metric's param_vars maps a parameter to the dbt variable the mart model computes it with (M-C05: x_seconds ->
service_level_seconds); the two must be equal in dbt_project.yml, or the mart column would not mean what the registry
says (Mac side 2026-11-16). param_vars is how the value reaches the model, not part of the definition: not hashed.

Output: {"metrics": [...], "marts": {table: {...}}}. marts.yaml (who may read a mart table, through which
dimensions, which columns are sensitive) is checked for form here and every metric's mart_table must be declared in
it; its columns are checked in the database, and its rules are synced into meta.mart_rule (change 0013), which the
service layer reads. Each metric: {code, version, domain, name_zh, name_en, name_ar, name_ar_state, definition, grain, aggregation,
measure, entity_date, retrospective, params, dimensions, source_tables, depends_unverified, published,
mart_table, mart_column, mart_numerator, mart_denominator, effective_from, definition_sha256}.
Exit code 0 with JSON on stdout; 1 with every problem found on stderr.
"""

import hashlib
import json
import re
import sys

import yaml

REQUIRED_TEXT = ["code", "domain", "name_zh", "name_en", "name_ar", "definition", "grain", "aggregation",
                 "entity_date"]
DEFINING = ["definition", "grain", "aggregation", "measure", "entity_date", "params", "retrospective"]
CODE = re.compile(r"^M-[A-Z]\d{2}$")
# A mart object is named mart.<domain>__<grain>__<content> (LD-02 chapter 10); a column is a plain identifier
MART_TABLE = re.compile(r"^mart\.[a-z][a-z0-9_]*$")
COLUMN = re.compile(r"^[a-z][a-z0-9_]*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def convert(doc, dbt_vars):
    problems, out, seen = [], [], set()
    metrics = doc.get("metrics") if isinstance(doc, dict) else None
    if not isinstance(metrics, list) or not metrics:
        return None, ["'metrics' must be a non-empty list"]
    for i, m in enumerate(metrics):
        where = f"metrics[{i}] {m.get('code', '?') if isinstance(m, dict) else '?'}"
        if not isinstance(m, dict):
            problems.append(f"{where}: not a mapping")
            continue
        bad = [k for k in REQUIRED_TEXT if not isinstance(m.get(k), str) or not m[k].strip()]
        if bad:
            problems.append(f"{where}: missing or empty: {', '.join(bad)}")
            continue
        code = m["code"]
        if not CODE.match(code):
            problems.append(f"{where}: code must look like M-C01")
        if code in seen:
            problems.append(f"{where}: code given twice")
        seen.add(code)
        if not isinstance(m.get("version"), int) or isinstance(m.get("version"), bool) or m["version"] < 1:
            problems.append(f"{where}: version must be an integer >= 1")
        for k in ("retrospective", "depends_unverified", "published"):
            if not isinstance(m.get(k), bool):
                problems.append(f"{where}: {k} must be true or false")
        if m.get("name_ar_state") not in ("draft", "confirmed"):
            problems.append(f"{where}: name_ar_state must be draft or confirmed")
        params = m.get("params", {})
        if not isinstance(params, dict):
            problems.append(f"{where}: params must be a mapping")
            params = {}
        pv = m.get("param_vars", {})
        if not isinstance(pv, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in pv.items()):
            problems.append(f"{where}: param_vars must map parameters to dbt variable names")
            pv = {}
        for param, var in pv.items():
            if param not in params:
                problems.append(f"{where}: param_vars names {param}, which is not in params")
            elif var not in dbt_vars:
                problems.append(f"{where}: param_vars names dbt variable {var}, which dbt_project.yml does not define")
            elif dbt_vars[var] != params[param]:
                problems.append(f"{where}: params {param} is {params[param]!r} but dbt variable {var} is "
                                f"{dbt_vars[var]!r}: the mart is computed with the dbt value. Make them equal (a new "
                                f"value is a new metric version and a new mart column, DD-97)")
        for k in ("dimensions", "source_tables"):
            if not isinstance(m.get(k), list) or not all(isinstance(x, str) for x in m[k]):
                problems.append(f"{where}: {k} must be a list of names")
        eff = m.get("effective_from")
        if eff is not None and not (isinstance(eff, str) and DATE.match(eff)):
            problems.append(f"{where}: effective_from must be a quoted date \"YYYY-MM-DD\"")

        mart = {k: m.get(k) for k in ("mart_table", "mart_column", "mart_numerator", "mart_denominator")}
        if mart["mart_table"] is not None:
            if not (isinstance(mart["mart_table"], str) and MART_TABLE.match(mart["mart_table"])):
                problems.append(f"{where}: mart_table must be mart.<name>")
            agg = m["aggregation"]
            if agg in ("count", "sum"):
                need, forbid = ["mart_column"], ["mart_numerator", "mart_denominator"]
            elif agg in ("ratio", "avg"):
                need, forbid = ["mart_numerator", "mart_denominator"], ["mart_column"]
            else:
                need, forbid = [], []
                problems.append(f"{where}: aggregation {agg} cannot be served from additive mart columns "
                                f"(DD-97 item three); leave mart_table out until a mart is designed for it")
            for k in need:
                if not (isinstance(mart[k], str) and COLUMN.match(mart[k])):
                    problems.append(f"{where}: aggregation {agg} needs {k} (a column name)")
            for k in forbid:
                if mart[k] is not None:
                    problems.append(f"{where}: aggregation {agg} takes no {k}")
        elif any(v is not None for v in mart.values()):
            problems.append(f"{where}: mart columns given without mart_table")

        defining = {k: m.get(k) for k in DEFINING}
        defining["params"] = params
        out.append({
            "code": code, "version": m.get("version"), "domain": m["domain"],
            "name_zh": m["name_zh"], "name_en": m["name_en"], "name_ar": m["name_ar"],
            "name_ar_state": m.get("name_ar_state"),
            "definition": m["definition"], "grain": m["grain"], "aggregation": m["aggregation"],
            "measure": m.get("measure"), "entity_date": m["entity_date"],
            "retrospective": m.get("retrospective"), "params": params,
            "dimensions": m.get("dimensions"), "source_tables": m.get("source_tables"),
            "depends_unverified": m.get("depends_unverified"), "published": m.get("published"),
            **mart, "effective_from": eff,
            "definition_sha256": hashlib.sha256(
                json.dumps(defining, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
        })
    return out, problems


ROLES = {"call_taker", "dispatcher", "supervisor", "admin"}
ROW_FILTERS = {"none", "department", "self"}


def convert_marts(doc):
    """Check marts.yaml's form; return ({table: rules}, problems)."""
    problems = []
    tables = doc.get("tables") if isinstance(doc, dict) else None
    if not isinstance(tables, dict) or not tables:
        return {}, ["marts.yaml: 'tables' must be a non-empty mapping"]
    out = {}
    for name, t in tables.items():
        where = f"marts.yaml {name}"
        if not (isinstance(name, str) and MART_TABLE.match(name)) or not isinstance(t, dict):
            problems.append(f"{where}: must be mart.<name> with a mapping of rules")
            continue
        roles = t.get("roles")
        if not isinstance(roles, list) or not roles or not set(roles) <= ROLES:
            problems.append(f"{where}: roles must be a non-empty list of {sorted(ROLES)}")
        rf = t.get("row_filter")
        if rf not in ROW_FILTERS:
            problems.append(f"{where}: row_filter must be one of {sorted(ROW_FILTERS)}")
        if (rf == "self") != ("self_column" in t):
            problems.append(f"{where}: self_column is given exactly when row_filter is self")
        dims = t.get("dimensions")
        if not isinstance(dims, dict) or not dims or not all(
                isinstance(k, str) and COLUMN.match(k) and isinstance(v, str) and COLUMN.match(v)
                for k, v in dims.items()):
            problems.append(f"{where}: dimensions must map names to columns")
        elif rf == "department" and "agency_type" not in dims:
            # the query API filters on the column of this dimension (DD-99 note 2026-11-17)
            problems.append(f"{where}: row_filter department needs the dimension agency_type (the department column)")
            dims = {}
        if not set(t.get("required_dimensions", [])) <= set(dims):
            problems.append(f"{where}: required_dimensions must be dimensions of the table")
        attrs = t.get("attributes", {})
        if not isinstance(attrs, dict) or not set(attrs) <= set(dims) or not all(
                isinstance(v, list) and all(isinstance(c, str) and COLUMN.match(c) for c in v) for v in attrs.values()):
            problems.append(f"{where}: attributes must map dimensions to lists of columns")
            attrs = {}
        sens = t.get("sensitive_columns", {})
        if not isinstance(sens, dict) or not all(
                isinstance(c, str) and COLUMN.match(c) and isinstance(r, list) and set(r) <= ROLES
                for c, r in sens.items()):
            problems.append(f"{where}: sensitive_columns must map columns to lists of roles")
            sens = {}
        columns = sorted(set(dims.values()) | {c for v in attrs.values() for c in v} | set(sens)
                         | ({t["self_column"]} if isinstance(t.get("self_column"), str) else set()))
        grain = t.get("grain")
        if not isinstance(grain, str) or not grain.strip():
            problems.append(f"{where}: grain must be given (what one row is)")
        rules = {"grain": grain, "roles": roles, "row_filter": rf, "self_column": t.get("self_column"),
                 "dimensions": dims, "required_dimensions": t.get("required_dimensions", []),
                 "attributes": attrs, "sensitive_columns": sens}
        out[name] = {**rules, "columns": columns,
                     "rules_sha256": hashlib.sha256(
                         json.dumps(rules, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()}
    return out, problems


def main(argv):
    if len(argv) != 4:
        print("usage: metrics_to_json.py <metrics.yaml> <marts.yaml> <dbt_project.yml>", file=sys.stderr)
        return 1
    with open(argv[1], encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    with open(argv[2], encoding="utf-8") as fh:
        marts_doc = yaml.safe_load(fh)
    with open(argv[3], encoding="utf-8") as fh:
        dbt_vars = (yaml.safe_load(fh) or {}).get("vars") or {}
    out, problems = convert(doc, dbt_vars)
    marts, mart_problems = convert_marts(marts_doc)
    problems += mart_problems
    for m in out or []:
        if m["mart_table"] is not None and m["mart_table"] not in marts:
            problems.append(f"{m['code']}: mart_table {m['mart_table']} is not declared in marts.yaml")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    text = json.dumps({"metrics": out, "marts": marts}, ensure_ascii=False)
    if "$metrics_json$" in text:      # the dollar-quote tag 46-metrics wraps the array in
        print("a metric's text contains $metrics_json$", file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
