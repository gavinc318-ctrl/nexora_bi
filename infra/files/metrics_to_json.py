"""Print metrics/metrics.yaml as one JSON array, for metric_sync.sql (DD-97 · LD-03 chapter 5).

Usage:  <venv-ingest>/bin/python metrics_to_json.py <metrics.yaml>

46-metrics.yml runs this on the target, over the release directory's copy, with venv-ingest's PyYAML. It checks
what the database cannot see from one row -- every field present and typed, codes unique, the mart mapping complete
for its aggregation -- and computes definition_sha256 over the fields that define a metric (LD-03 chapter 6: a
change to any of them is a change of definition). Whether the mart columns exist is checked in the database.

Each element: {code, version, domain, name_zh, name_en, name_ar, name_ar_state, definition, grain, aggregation,
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


def convert(doc):
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


def main(argv):
    if len(argv) != 2:
        print("usage: metrics_to_json.py <metrics.yaml>", file=sys.stderr)
        return 1
    with open(argv[1], encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    out, problems = convert(doc)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    text = json.dumps(out, ensure_ascii=False)
    if "$metrics_json$" in text:      # the dollar-quote tag 46-metrics wraps the array in
        print("a metric's text contains $metrics_json$", file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
