"""Print metrics/metrics.yaml and metrics/marts.yaml as one JSON object, for metric_sync.sql (DD-97 · DD-99 ·
LD-03 chapter 5).

Usage:  <venv-ingest>/bin/python metrics_to_json.py <metrics.yaml> <marts.yaml> <dbt_project.yml> <reports dir>

46-metrics.yml runs this on the target, over the release directory's copy, with venv-ingest's PyYAML. It checks
what the database cannot see from one row -- every field present and typed, codes unique, the mart mapping complete
for its aggregation -- and computes definition_sha256 over the fields that define a metric (LD-03 chapter 6: a
change to any of them is a change of definition). Whether the mart columns exist is checked in the database.

A metric's param_vars maps a parameter to the dbt variable the mart model computes it with (M-C05: x_seconds ->
service_level_seconds); the two must be equal in dbt_project.yml, or the mart column would not mean what the registry
says (Mac side 2026-11-16). param_vars is how the value reaches the model, not part of the definition: not hashed.

Report definitions (reports/<id>.yaml, DD-102 · LD-05 · LD-04 3.5) are checked against both: every metric published
and implemented, a query's metrics in one mart table, its dimensions those of the table and its required dimensions
among them, and the report's roles within the roles of every table it reads -- a report a role can see in the
catalogue must not answer 403 when opened (DD-102 note 2026-11-25). They are synced into meta.report_definition
(change 0018). The format is described in reports/README.md.

Output: {"metrics": [...], "marts": {table: {...}}, "reports": {id: {...}}}. marts.yaml (who may read a mart table, through which
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


# ---------------------------------------------------------------- report definitions (DD-102)
REPORT_ID = re.compile(r"^[RD]-\d{2}$")
REPORT_KEYS = {"id", "kind", "domain", "title", "title_ar_state", "roles", "published", "defaults", "refresh",
               "print", "source_note", "filters", "formats", "queries", "views"}
DOMAINS = {"calls", "incidents", "resources", "performance", "operations"}
RANGES = {"today", "yesterday", "last_7_days", "last_30_days"}
FORMATS = {"integer", "decimal", "percent", "duration"}
GRANULARITIES = {"day", "week", "month", "quarter", "year"}
CHARTS = {"bar", "line"}
STACKS = {"none", "stacked", "percent"}


def _text(v, where, problems, ar_optional=True):
    """{en, ar}: en required; ar may be null while the Arabic is not written yet (title_ar_state draft)."""
    if not isinstance(v, dict) or set(v) - {"en", "ar"} or not isinstance(v.get("en"), str) or not v["en"].strip():
        problems.append(f"{where}: must be {{en: text, ar: text or null}}")
        return {"en": None, "ar": None}
    ar = v.get("ar")
    if ar is not None and (not isinstance(ar, str) or not ar.strip()):
        problems.append(f"{where}: ar must be text or null")
    return {"en": v["en"], "ar": ar}


def _names(v, where, problems, what="a list of names"):
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v) or len(set(v)) != len(v):
        problems.append(f"{where}: must be {what}, each once")
        return []
    return v


def _unknown(d, allowed, where, problems):
    extra = sorted(set(d) - set(allowed))
    if extra:
        problems.append(f"{where}: unknown key(s) {', '.join(extra)}")


def convert_report(doc, file_id, metrics, marts):
    """Check one report definition; return (normalized definition, problems). metrics: code -> latest converted
    metric; marts: the converted marts.yaml."""
    p = []
    w = f"reports/{file_id}.yaml"
    if not isinstance(doc, dict):
        return None, [f"{w}: not a mapping"]
    _unknown(doc, REPORT_KEYS, w, p)
    rid = doc.get("id")
    if rid != file_id or not REPORT_ID.match(str(rid)):
        p.append(f"{w}: id must be the file name, like R-01 or D-06")
    kind = doc.get("kind")
    if kind not in ("report", "dashboard") or (kind == "report") != str(rid).startswith("R-"):
        p.append(f"{w}: kind must be report (R-nn) or dashboard (D-nn)")
    if doc.get("domain") not in DOMAINS:
        p.append(f"{w}: domain must be one of {sorted(DOMAINS)}")
    title = _text(doc.get("title"), f"{w} title", p)
    ar_state = doc.get("title_ar_state")
    if ar_state not in ("draft", "confirmed") or (title["ar"] is None and ar_state != "draft"):
        p.append(f"{w}: title_ar_state must be draft or confirmed (draft while title.ar is null)")
    roles = _names(doc.get("roles"), f"{w} roles", p)
    if not roles or not set(roles) <= ROLES:
        p.append(f"{w}: roles must be a non-empty list of {sorted(ROLES)}")
    if not isinstance(doc.get("published"), bool):
        p.append(f"{w}: published must be true or false")
    defaults = doc.get("defaults")
    if not isinstance(defaults, dict):
        p.append(f"{w}: defaults must be a mapping")
        defaults = {}
    _unknown(defaults, {"range", "data_version"}, f"{w} defaults", p)
    if defaults.get("range") not in RANGES:
        p.append(f"{w}: defaults.range must be one of {sorted(RANGES)}")
    if defaults.get("data_version") not in ("latest", "as_closed"):
        p.append(f"{w}: defaults.data_version must be latest or as_closed")
    if defaults.get("range") == "today" and defaults.get("data_version") == "as_closed":
        p.append(f"{w}: today is never closed; defaults.data_version must be latest")
    refresh = doc.get("refresh")
    if refresh == {"mode": "on_open"}:
        pass
    elif isinstance(refresh, dict) and set(refresh) == {"mode", "seconds"} and refresh["mode"] == "interval" \
            and isinstance(refresh["seconds"], int) and not isinstance(refresh["seconds"], bool) \
            and refresh["seconds"] >= 60:
        if defaults.get("data_version") != "latest":
            p.append(f"{w}: an interval refresh reads the provisional rows; defaults.data_version must be latest")
    else:
        p.append(f"{w}: refresh must be {{mode: on_open}} or {{mode: interval, seconds: >= 60}}")
    pr = doc.get("print")
    if not (isinstance(pr, dict) and set(pr) == {"orientation"} and pr["orientation"] in ("portrait", "landscape")):
        p.append(f"{w}: print must be {{orientation: portrait or landscape}}")
    note = doc.get("source_note")
    note = None if note is None else _text(note, f"{w} source_note", p)

    # Queries: one call of POST /v1/query each, so one mart table each
    queries, tables = {}, {}
    qs = doc.get("queries")
    if not isinstance(qs, dict) or not qs:
        p.append(f"{w}: queries must be a non-empty mapping of name -> query")
        qs = {}
    for name, q in qs.items():
        qw = f"{w} queries.{name}"
        if not (isinstance(name, str) and COLUMN.match(name)) or not isinstance(q, dict):
            p.append(f"{qw}: must be a lower-case name with a mapping")
            continue
        _unknown(q, {"metrics", "dimensions", "granularity"}, qw, p)
        codes = _names(q.get("metrics"), f"{qw} metrics", p, "a non-empty list of metric codes")
        dims = _names(q.get("dimensions", []), f"{qw} dimensions", p)
        gran = q.get("granularity", "day")
        if gran not in GRANULARITIES:
            p.append(f"{qw}: granularity must be one of {sorted(GRANULARITIES)}")
        if not codes:
            p.append(f"{qw}: metrics must not be empty")
        mart_tables = set()
        for c in codes:
            m = metrics.get(c)
            if m is None:
                p.append(f"{qw}: metric {c} is not in metrics.yaml")
            elif not m["published"]:
                p.append(f"{qw}: metric {c} is not published")
            elif m["mart_table"] is None:
                p.append(f"{qw}: metric {c} is not implemented (no mart_table)")
            else:
                mart_tables.add(m["mart_table"])
        if len(mart_tables) > 1:
            p.append(f"{qw}: metrics read {sorted(mart_tables)}; one query reads one mart table -- split it")
        table = next(iter(mart_tables)) if len(mart_tables) == 1 else None
        rule = marts.get(table) if table else None
        if rule is not None:
            bad = [d for d in dims if d not in rule["dimensions"]]
            if bad:
                p.append(f"{qw}: {table} has no dimension(s) {', '.join(bad)} (it has {sorted(rule['dimensions'])})")
            missing = [d for d in rule["required_dimensions"] if d not in dims]
            if missing:
                p.append(f"{qw}: {table} must be queried with {', '.join(missing)} as an output dimension")
            beyond = [r for r in roles if r not in rule["roles"]]
            if beyond:
                p.append(f"{qw}: the report is for {', '.join(beyond)}, who may not read {table} "
                         f"(roles {rule['roles']}): the catalogue would list a report that answers 403")
            tables[name] = table
        queries[name] = {"metrics": codes, "dimensions": dims, "granularity": gran}

    # Filters: a dimension the reader may narrow; every query must have it, or the filter would not apply to all
    filters = []
    fl = doc.get("filters", [])
    if not isinstance(fl, list):
        p.append(f"{w}: filters must be a list")
        fl = []
    for i, f in enumerate(fl):
        fw = f"{w} filters[{i}]"
        if not isinstance(f, dict) or not isinstance(f.get("dimension"), str):
            p.append(f"{fw}: must be {{dimension, roles (optional)}}")
            continue
        _unknown(f, {"dimension", "roles"}, fw, p)
        froles = _names(f.get("roles", roles), f"{fw} roles", p)
        if not set(froles) <= set(roles) or not froles:
            p.append(f"{fw}: roles must be some of the report's roles")
        for name, table in tables.items():
            if f["dimension"] not in marts[table]["dimensions"]:
                p.append(f"{fw}: query {name} ({table}) has no dimension {f['dimension']}")
        filters.append({"dimension": f["dimension"], "roles": froles})
    if len({f["dimension"] for f in filters}) != len(filters):
        p.append(f"{w}: a dimension is given twice in filters")

    used = {c for q in queries.values() for c in q["metrics"]}
    formats = doc.get("formats", {})
    if not isinstance(formats, dict) or not all(isinstance(v, str) and v in FORMATS for v in formats.values()):
        p.append(f"{w}: formats must map metric codes to one of {sorted(FORMATS)}")
        formats = {}
    for c in formats:
        if c not in used:
            p.append(f"{w}: formats names {c}, which no query reads")

    # Views: what is shown, in order. A table joins queries on their (identical) dimensions; a chart draws one query
    views, shown = [], set()
    vs = doc.get("views")
    if not isinstance(vs, list) or not vs:
        p.append(f"{w}: views must be a non-empty list")
        vs = []
    for i, v in enumerate(vs):
        vw = f"{w} views[{i}]"
        if not isinstance(v, dict) or v.get("kind") not in ("table", "chart"):
            p.append(f"{vw}: kind must be table or chart")
            continue
        vt = None if v.get("title") is None else _text(v["title"], f"{vw} title", p)
        if v["kind"] == "table":
            _unknown(v, {"kind", "title", "queries", "columns"}, vw, p)
            names = _names(v.get("queries"), f"{vw} queries", p, "a non-empty list of query names")
            if not names or not all(n in queries for n in names):
                p.append(f"{vw}: queries must name queries of the report")
                names = [n for n in names if n in queries]
            if len({tuple(sorted(queries[n]["dimensions"])) for n in names}) > 1 \
                    or len({queries[n]["granularity"] for n in names}) > 1:
                p.append(f"{vw}: the queries of one table must have the same dimensions and granularity "
                         f"(rows are joined on them)")
            avail = [c for n in names for c in queries[n]["metrics"]]
            if len(set(avail)) != len(avail):
                p.append(f"{vw}: a metric is read by two of the table's queries")
            cols = _names(v.get("columns", avail), f"{vw} columns", p, "a list of metric codes")
            if not set(cols) <= set(avail):
                p.append(f"{vw}: columns must be metrics of its queries")
            shown |= set(names)
            views.append({"kind": "table", "title": vt, "queries": names, "columns": cols})
        else:
            _unknown(v, {"kind", "title", "query", "chart", "stack", "horizontal", "x", "split", "series", "sort"},
                     vw, p)
            q = queries.get(v.get("query"))
            if q is None:
                p.append(f"{vw}: query must name one of queries")
                continue
            if v.get("chart") not in CHARTS:
                p.append(f"{vw}: chart must be one of {sorted(CHARTS)}")
            stack = v.get("stack", "none")
            if stack not in STACKS or (stack != "none" and v.get("chart") != "bar"):
                p.append(f"{vw}: stack must be one of {sorted(STACKS)}, and only for bar")
            if not isinstance(v.get("horizontal", False), bool):
                p.append(f"{vw}: horizontal must be true or false")
            x, split = v.get("x"), v.get("split")
            if sorted(d for d in (x, split) if d is not None) != sorted(q["dimensions"]) or x is None:
                p.append(f"{vw}: x (and split, when given) must be exactly the query's dimensions {q['dimensions']}")
            series = _names(v.get("series", q["metrics"]), f"{vw} series", p, "a list of metric codes")
            if not set(series) <= set(q["metrics"]):
                p.append(f"{vw}: series must be metrics of query {v['query']}")
            if split is not None and len(series) != 1:
                p.append(f"{vw}: with split, one series (the split members are the series)")
            sort = v.get("sort")
            if sort is not None and not (isinstance(sort, dict) and set(sort) == {"by", "order"}
                                         and sort["by"] in q["metrics"] and sort["order"] in ("asc", "desc")):
                p.append(f"{vw}: sort must be {{by: a metric of the query, order: asc or desc}}")
            shown.add(v["query"])
            views.append({"kind": "chart", "title": vt, "query": v["query"], "chart": v.get("chart"),
                          "stack": stack, "horizontal": v.get("horizontal", False), "x": x, "split": split,
                          "series": series, "sort": sort})
    for name in queries:
        if name not in shown:
            p.append(f"{w}: query {name} is not shown by any view")

    out = {"id": rid, "kind": kind, "domain": doc.get("domain"), "title": title, "title_ar_state": ar_state,
           "roles": roles, "published": doc.get("published"), "defaults": defaults, "refresh": refresh,
           "print": pr, "source_note": note, "filters": filters, "formats": formats, "queries": queries,
           "views": views}
    out["definition_sha256"] = hashlib.sha256(
        json.dumps(out, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return out, p


def convert_reports(directory, metrics_out, marts):
    """Every reports/*.yaml in directory; ({id: definition}, problems). No directory or no file: no reports."""
    import glob
    import os
    latest = {m["code"]: m for m in metrics_out}
    out, problems = {}, []
    for path in sorted(glob.glob(os.path.join(directory, "*.yaml"))):
        file_id = os.path.basename(path)[:-len(".yaml")]
        with open(path, encoding="utf-8") as fh:
            try:
                doc = yaml.safe_load(fh)
            except yaml.YAMLError as exc:
                problems.append(f"reports/{file_id}.yaml: {exc}")
                continue
        rep, p = convert_report(doc, file_id, latest, marts)
        problems += p
        if rep is not None:
            out[file_id] = rep
    return out, problems


def main(argv):
    if len(argv) != 5:
        print("usage: metrics_to_json.py <metrics.yaml> <marts.yaml> <dbt_project.yml> <reports dir>", file=sys.stderr)
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
    reports, report_problems = convert_reports(argv[4], out or [], marts)
    problems += report_problems
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    text = json.dumps({"metrics": out, "marts": marts, "reports": reports}, ensure_ascii=False)
    if "$metrics_json$" in text:      # the dollar-quote tag 46-metrics wraps the array in
        print("a metric's text contains $metrics_json$", file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
