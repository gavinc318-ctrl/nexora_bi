"""POST /v1/query: metrics by dimensions over a date range (DD-99 · LD-04 3.5 and its review corrections).

The order matters, and is the one DD-99 review item five fixes:
  1. resolve the metrics: their versions over the range (each day uses the version in effect that day unless one
     is asked for), the one mart table they must share, its read rule
  2. check what the user may do: the table, the fields named (output and filters), the row filter that applies
  3. choose, per day, the publication to read -- from meta.mart_day_publication, BEFORE any filtering: latest (the
     newest version, provisional included), as_closed (close_version 1 only) or a given close_version. Filtering
     first and then taking the newest version per member would bring back a member's old version when the member
     is gone from the new one
  4. read only those rows, filter, sum the additive columns per output row; ratios and averages are the sum of
     the numerator over the sum of the denominator, computed here (DD-97 item three), null when the denominator is 0
SQL is parameterised; identifiers come only from the read rules and the registry, written as two-part names (DD-93).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field

from psycopg import errors as pgerrors
from psycopg import sql

from .catalog import Catalog, MartRule, MetricVersion
from .config import MAX_ROWS, QUERY_TIMEOUT
from .context import PermissionContext
from .errors import (DimensionInvalid, IncompatibleGrain, MetricNotImplemented, MetricUnknown, MetricVersionBoundary,
                     MetricVersionUnknown, RequestInvalid, ResultTooLarge, Timeout)

GRANULARITIES = ("day", "week", "month", "quarter", "year")
MAX_RANGE_DAYS = 3 * 366


@dataclass(frozen=True)
class MetricRef:
    code: str
    version: int | None = None


@dataclass(frozen=True)
class QueryRequest:
    metrics: list[MetricRef]
    range_from: dt.date
    range_to: dt.date                       # exclusive
    dimensions: list[str] = field(default_factory=list)
    filters: dict[str, list[str]] = field(default_factory=dict)
    granularity: str = "day"
    data_version: str | int = "latest"      # latest · as_closed · a close_version
    locale: str = "en"

    def digest(self) -> str:
        """The request in a canonical form, hashed: the audit's request_digest and part of the cache key."""
        canon = {"metrics": [[m.code, m.version] for m in self.metrics], "from": self.range_from.isoformat(),
                 "to": self.range_to.isoformat(), "dimensions": list(self.dimensions),
                 "filters": {k: sorted(v) for k, v in sorted(self.filters.items())},
                 "granularity": self.granularity, "data_version": self.data_version}
        return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


@dataclass
class Segment:
    """One metric version over a part of the range: [start, end)."""
    version: MetricVersion
    start: dt.date
    end: dt.date


def date_key(d: dt.date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def from_key(k: int) -> dt.date:
    return dt.date(k // 10000, k // 100 % 100, k % 100)


# ---------------------------------------------------------------- 1. metrics, versions, table
def resolve_metric(catalog: Catalog, ref: MetricRef, start: dt.date, end: dt.date) -> list[Segment]:
    versions = catalog.metrics.get(ref.code)
    if not versions:
        raise MetricUnknown(f"metric {ref.code} is not in the registry", metric=ref.code)
    if ref.version is not None:
        chosen = [v for v in versions if v.version == ref.version]
        if not chosen:
            raise MetricVersionUnknown(f"metric {ref.code} has no version {ref.version}",
                                       metric=ref.code, versions=[v.version for v in versions])
        segments = [Segment(chosen[0], start, end)]
    else:
        segments = []
        for i, v in enumerate(versions):
            nxt = versions[i + 1].effective_from if i + 1 < len(versions) else dt.date.max
            s, e = max(v.effective_from, start), min(nxt, end)
            if s < e:
                segments.append(Segment(v, s, e))
        if not segments:   # the range lies before the first version's effective_from
            raise MetricNotImplemented(f"metric {ref.code} has no version in effect in the range", metric=ref.code)
    for s in segments:
        if not s.version.implemented:
            raise MetricNotImplemented(f"metric {ref.code} version {s.version.version} is registered but not "
                                       f"implemented", metric=ref.code, version=s.version.version)
    return segments


def plan_metrics(catalog: Catalog, req: QueryRequest) -> tuple[dict[str, list[Segment]], MartRule]:
    plan = {ref.code: resolve_metric(catalog, ref, req.range_from, req.range_to) for ref in req.metrics}
    tables = sorted({s.version.mart_table for segs in plan.values() for s in segs})
    if len(tables) > 1:
        raise IncompatibleGrain("the metrics of one request must come from one mart table; ask for them in "
                                "separate requests", tables=tables)
    rule = catalog.rules.get(tables[0])
    if rule is None:
        raise MetricNotImplemented(f"{tables[0]} is not declared in the read rules (metrics/marts.yaml)",
                                   table=tables[0])
    return plan, rule


def check_request(req: QueryRequest, plan: dict[str, list[Segment]], rule: MartRule, ctx: PermissionContext) -> None:
    if not req.metrics:
        raise RequestInvalid("name at least one metric")
    if len({m.code for m in req.metrics}) != len(req.metrics):
        raise RequestInvalid("a metric is named twice")
    if req.granularity not in GRANULARITIES:
        raise RequestInvalid(f"granularity must be one of {GRANULARITIES}")
    if req.range_to <= req.range_from:
        raise RequestInvalid("range: to must be after from (from inclusive, to exclusive)")
    if (req.range_to - req.range_from).days > MAX_RANGE_DAYS:
        raise RequestInvalid(f"range: at most {MAX_RANGE_DAYS} days")
    if isinstance(req.data_version, str) and req.data_version not in ("latest", "as_closed"):
        raise RequestInvalid("data_version must be latest, as_closed or a close_version number")
    if isinstance(req.data_version, int) and req.data_version < 0:
        raise RequestInvalid("data_version: a close_version is 0 or more")

    ctx.check_table(rule)
    unknown = [d for d in list(req.dimensions) + list(req.filters) if d not in rule.dimensions]
    if unknown:
        raise DimensionInvalid(f"{rule.table} has no dimension {', '.join(unknown)}",
                               dimensions=sorted(rule.dimensions))
    if len(set(req.dimensions)) != len(req.dimensions):
        raise DimensionInvalid("a dimension is named twice")
    if "date" in req.filters:
        raise DimensionInvalid("the date is limited by range, not by a filter")
    missing = sorted(rule.required_dimensions - set(req.dimensions))
    if missing:
        raise DimensionInvalid(f"{rule.table} must be queried with {', '.join(missing)} as an output dimension: "
                               f"a column repeated on each of its members would be counted several times",
                               required=missing)
    ctx.check_fields(rule, {d: rule.dimensions[d] for d in list(req.dimensions) + list(req.filters)})
    for name, members in req.filters.items():
        if not members:
            raise RequestInvalid(f"filter {name}: name at least one member")

    multi = sorted(code for code, segs in plan.items() if len(segs) > 1)
    if multi and not ("date" in req.dimensions and req.granularity == "day"):
        raise MetricVersionBoundary(
            f"the range crosses the effective date of a new definition of {', '.join(multi)}: the two definitions' "
            f"numbers are not added up. Query by day, or split the range, or name a metric_version",
            metrics={c: [{"version": s.version.version, "from": s.start.isoformat(), "to": s.end.isoformat()}
                         for s in plan[c]] for c in multi})


# ---------------------------------------------------------------- 3. which publication per day
def choose_publications(conn, rule: MartRule, req: QueryRequest) -> tuple[list[dict], list[dict]]:
    """Every day of the range with its state; and the (day, close_version, publication_seq) to read."""
    regs: dict[int, list[tuple]] = {}
    for k, ver, seq, at in conn.execute(
            """SELECT p.date_key, p.close_version, p.publication_seq, p.published_at
                 FROM meta.mart_day_publication p
                WHERE p.mart_table = %s AND p.date_key >= %s AND p.date_key < %s""",
            (rule.table, date_key(req.range_from), date_key(req.range_to))):
        regs.setdefault(k, []).append((ver, seq, at))
    days, chosen = [], []
    d = req.range_from
    while d < req.range_to:
        k, entries = date_key(d), regs.get(date_key(d), [])
        pick, status = None, None
        if not entries:
            status = "not_built"
        elif req.data_version == "latest":
            pick = max(entries, key=lambda e: (e[0], e[1]))
        elif req.data_version == "as_closed":
            pick = next((e for e in entries if e[0] == 1), None)
            status = None if pick else "not_closed"
        else:
            hits = [e for e in entries if e[0] == req.data_version]
            pick = max(hits, key=lambda e: e[1]) if hits else None
            status = None if pick else ("not_closed" if all(e[0] == 0 for e in entries) else "version_not_found")
        if pick:
            chosen.append({"date_key": k, "close_version": pick[0], "publication_seq": pick[1],
                           "published_at": pick[2]})
            days.append({"date": d.isoformat(), "status": "published", "close_version": pick[0],
                         "publication_seq": pick[1]})
        else:
            days.append({"date": d.isoformat(), "status": status})
        d += dt.timedelta(days=1)
    return days, chosen


# ---------------------------------------------------------------- 4. the rows
def period_sql(granularity: str) -> sql.Composable:
    d = sql.SQL("to_date(t.date_key::text, 'YYYYMMDD')")
    return {
        "day": d,
        "week": sql.SQL("({d} - extract(dow FROM {d})::int)").format(d=d),     # weeks start on Sunday
        "month": sql.SQL("date_trunc('month', {d})::date").format(d=d),
        "quarter": sql.SQL("date_trunc('quarter', {d})::date").format(d=d),
        "year": sql.SQL("date_trunc('year', {d})::date").format(d=d),
    }[granularity]


def chosen_join(chosen: list[dict]) -> tuple[sql.Composable, list]:
    return (sql.SQL("JOIN unnest(%s::integer[], %s::integer[], %s::bigint[]) AS c(date_key, close_version, "
                    "publication_seq) ON c.date_key = t.date_key AND c.close_version = t.close_version "
                    "AND c.publication_seq = t.publication_seq"),
            [[c["date_key"] for c in chosen], [c["close_version"] for c in chosen],
             [c["publication_seq"] for c in chosen]])


def where_sql(rule: MartRule, req: QueryRequest, ctx: PermissionContext) -> tuple[sql.Composable, list]:
    preds, params = [], []
    rf, rf_params = ctx.row_filter(rule)
    preds.append(rf)
    params += rf_params
    for name, members in sorted(req.filters.items()):
        preds.append(sql.SQL("{}::text = ANY(%s::text[])").format(sql.Identifier("t", rule.dimensions[name])))
        params.append([str(m) for m in members])
    return sql.SQL(" AND ").join(preds), params


def mart_columns(conn, rule: MartRule) -> set[str]:
    schema, table = rule.table.split(".", 1)
    return {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
        (schema, table))}


def run_query(conn, catalog: Catalog, ctx: PermissionContext, req: QueryRequest) -> dict:
    plan, rule = plan_metrics(catalog, req)
    check_request(req, plan, rule, ctx)
    where, where_params = where_sql(rule, req, ctx)       # refuses here when the row filter cannot be applied
    days, chosen = choose_publications(conn, rule, req)
    conn.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(QUERY_TIMEOUT)))

    out_dims = [d for d in req.dimensions if d != "date"]
    select, group = [], []
    if "date" in req.dimensions:
        select.append(sql.SQL("{} AS period").format(period_sql(req.granularity)))
        group.append(sql.SQL("period"))
    for i, d in enumerate(out_dims):
        select.append(sql.SQL("{}::text AS {}").format(sql.Identifier("t", rule.dimensions[d]), sql.Identifier(f"d{i}")))
        group.append(sql.Identifier(f"d{i}"))
    sums = []   # (metric code, segment index, column index)
    for code, segs in plan.items():
        for si, seg in enumerate(segs):
            for ci, col in enumerate(seg.version.columns):
                expr = sql.Identifier("t", col)
                if len(segs) > 1:
                    expr = sql.SQL("CASE WHEN t.date_key >= %s AND t.date_key < %s THEN {} END").format(expr)
                select.append(sql.SQL("sum({})").format(expr))
                sums.append((code, si, ci, len(segs) > 1, seg))
    seg_params = [p for (_, _, _, multi, seg) in sums if multi for p in (date_key(seg.start), date_key(seg.end))]

    rows = []
    if chosen:
        join, join_params = chosen_join(chosen)
        query = sql.SQL("SELECT {select} FROM {table} t {join} WHERE {where} {group} {order} LIMIT %s").format(
            select=sql.SQL(", ").join(select), table=sql.Identifier(*rule.table.split(".", 1)), join=join,
            where=where, group=sql.SQL("GROUP BY ") + sql.SQL(", ").join(group) if group else sql.SQL(""),
            order=sql.SQL("ORDER BY ") + sql.SQL(", ").join(group) if group else sql.SQL(""))
        try:
            rows = conn.execute(query, seg_params + join_params + where_params + [MAX_ROWS + 1]).fetchall()
        except pgerrors.QueryCanceled:
            raise Timeout(f"the query took longer than {QUERY_TIMEOUT}") from None
        if len(rows) > MAX_ROWS:
            raise ResultTooLarge(f"more than {MAX_ROWS} rows: narrow the range, the dimensions or the filters, or "
                                 f"use the export", limit=MAX_ROWS)
        if not group and rows and all(v is None for v in rows[0]):
            rows = []   # an aggregate over no rows

    result_rows = []
    n_lead = (1 if "date" in req.dimensions else 0) + len(out_dims)
    for r in rows:
        item, pos = {}, 0
        if "date" in req.dimensions:
            item["date"] = r[0].isoformat()
            pos = 1
        for i, d in enumerate(out_dims):
            item[d] = r[pos + i]
        values = r[n_lead:]
        versions = {}
        for code, segs in plan.items():
            # the segment of this row: the only one with sums when the range crosses versions (day rows)
            idx = [k for k, s in enumerate(sums) if s[0] == code]
            best = None
            for si, seg in enumerate(segs):
                vals = [values[k] for k in idx if sums[k][1] == si]
                if len(segs) == 1 or any(v is not None for v in vals):
                    best = (seg, vals)
                    break
            if best is None:
                item[code] = None
                continue
            seg, vals = best
            versions[code] = seg.version.version
            if seg.version.mart_column:
                item[code] = int(vals[0]) if vals[0] is not None else None
            else:
                num, den = vals
                item[code] = (float(num) / float(den)) if num is not None and den else None
        if any(len(s) > 1 for s in plan.values()):
            item["metric_versions"] = versions
        result_rows.append(item)

    # meta fields over the same rows and the same filters (in the user's scope, DD-99 review item one)
    cols = mart_columns(conn, rule)
    in_progress, has_unlinked = None, None
    extra = []
    if "incomplete_calls" in cols:
        extra.append(sql.SQL("sum(t.incomplete_calls) FILTER (WHERE t.close_version = 0)"))
    key_cols = [c for c in ("queue_key", "agent_key") if c in cols]
    if key_cols:
        extra.append(sql.SQL("coalesce(bool_or({}), false)").format(
            sql.SQL(" OR ").join(sql.SQL("{} = 'unlinked'").format(sql.Identifier("t", c)) for c in key_cols)))
    if chosen and extra:
        join, join_params = chosen_join(chosen)
        m = conn.execute(sql.SQL("SELECT {} FROM {} t {} WHERE {}").format(
            sql.SQL(", ").join(extra), sql.Identifier(*rule.table.split(".", 1)), join, where),
            join_params + where_params).fetchone()
        i = 0
        if "incomplete_calls" in cols:
            in_progress = int(m[0] or 0)
            i = 1
        if key_cols:
            has_unlinked = bool(m[i])

    availability = [{"metric": r[0], "year": r[1], "state": r[2], "limiting_column": r[3]} for r in conn.execute(
        """SELECT metric_code, year, state, limiting_column FROM meta.metric_availability
            WHERE metric_code = ANY(%s) AND state <> 'available' AND year BETWEEN %s AND %s ORDER BY 1, 2""",
        ([m.code for m in req.metrics], req.range_from.year, (req.range_to - dt.timedelta(days=1)).year))]
    empty_reason = None
    if not result_rows:
        empty_reason = "out_of_availability" if availability else "no_data"

    meta = {
        "table": rule.table,
        "data_version": req.data_version,
        "metrics": [{
            "code": code, "name": segs[0].version.name(req.locale), "aggregation": segs[0].version.aggregation,
            "depends_unverified": any(s.version.depends_unverified for s in segs),
            "versions": [{"metric_version": s.version.version, "from": s.start.isoformat(), "to": s.end.isoformat()}
                         for s in segs]} for code, segs in plan.items()],
        "publication_seq": max((c["publication_seq"] for c in chosen), default=None),
        "data_as_of": max((c["published_at"] for c in chosen), default=None),
        "days": days,
        "unclosed_dates": [d["date"] for d in days if d["status"] == "not_closed"],
        "in_progress_calls": in_progress,
        "has_unlinked": has_unlinked,
        "empty_reason": empty_reason,
        "availability": availability,
        "row_scope": ctx.scope(rule),
    }
    if meta["data_as_of"] is not None:
        meta["data_as_of"] = meta["data_as_of"].isoformat()
    return {"rows": result_rows, "meta": meta, "_sensitive": [], "_table": rule.table}


# ---------------------------------------------------------------- dimension members
def run_members(conn, catalog: Catalog, ctx: PermissionContext, metric: str, dimension: str,
                attributes: list[str], range_from: dt.date, range_to: dt.date) -> dict:
    """The members of a dimension of the metric's table, in the user's scope (the same row filter as a query),
    with the attributes asked for. A sensitive attribute the role may not see refuses the request."""
    req = QueryRequest(metrics=[MetricRef(metric)], range_from=range_from, range_to=range_to,
                       dimensions=[dimension] if dimension != "date" else [])
    plan, rule = plan_metrics(catalog, req)
    ctx.check_table(rule)
    if dimension not in rule.dimensions or dimension == "date":
        raise DimensionInvalid(f"{rule.table} has no member dimension {dimension}",
                               dimensions=sorted(d for d in rule.dimensions if d != "date"))
    allowed = rule.attributes.get(dimension, [])
    unknown = [a for a in attributes if a not in allowed]
    if unknown:
        raise DimensionInvalid(f"dimension {dimension} has no attribute {', '.join(unknown)}", attributes=allowed)
    ctx.check_fields(rule, {dimension: rule.dimensions[dimension], **{a: a for a in attributes}})
    where, where_params = ctx.row_filter(rule)
    _, chosen = choose_publications(conn, rule, req)
    conn.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(QUERY_TIMEOUT)))
    members = []
    if chosen:
        join, join_params = chosen_join(chosen)
        cols = [sql.SQL("{}::text").format(sql.Identifier("t", rule.dimensions[dimension]))] + \
               [sql.SQL("{}::text").format(sql.Identifier("t", a)) for a in attributes]
        try:
            rows = conn.execute(sql.SQL("SELECT DISTINCT {} FROM {} t {} WHERE {} ORDER BY 1 LIMIT %s").format(
                sql.SQL(", ").join(cols), sql.Identifier(*rule.table.split(".", 1)), join, where),
                join_params + where_params + [MAX_ROWS + 1]).fetchall()
        except pgerrors.QueryCanceled:
            raise Timeout(f"the query took longer than {QUERY_TIMEOUT}") from None
        if len(rows) > MAX_ROWS:
            raise ResultTooLarge(f"more than {MAX_ROWS} members", limit=MAX_ROWS)
        members = [{"member": r[0], **{a: r[i + 1] for i, a in enumerate(attributes)}} for r in rows]
    return {"members": members,
            "meta": {"table": rule.table, "dimension": dimension, "row_scope": ctx.scope(rule),
                     "from": range_from.isoformat(), "to": range_to.isoformat()},
            "_sensitive": sorted(a for a in attributes if a in rule.sensitive_columns), "_table": rule.table}
