"""The report catalogue and definitions for the portal (DD-102 · LD-04 3.5 · LD-05 chapter 2).

A definition says what to query and how to lay it out (reports/README.md); the data still comes from POST /v1/query
under the same permissions, so holding a definition is not holding its data. Visibility is decided here, from the
report's roles, never by the portal hiding a link: a report the role may not see, an unpublished one and one that
does not exist all answer the same 403 report_not_permitted, audited, so a refusal does not tell which reports exist.

46-metrics refuses a report whose roles go beyond those of a mart table it reads, so a report listed in the
catalogue does not answer 403 when it is queried. The row filter still applies: a call taker opening R-04 gets the
definition, and POST /v1/query gives them only their own row.

Texts are given in the requested language. An Arabic text not written yet (null in the definition, title_ar_state
draft) falls back to the English one; the portal shows the draft state.
"""

from __future__ import annotations

from .catalog import Catalog
from .context import PermissionContext
from .errors import ReportNotPermitted

# Display format of a metric the definition does not name one for, by its aggregation
DEFAULT_FORMAT = {"count": "integer", "sum": "integer", "ratio": "percent", "avg": "decimal"}


def _text(value: dict | None, locale: str) -> str | None:
    if value is None:
        return None
    return (value.get("ar") if locale == "ar" else None) or value["en"]


def catalogue(catalog: Catalog, ctx: PermissionContext, locale: str) -> dict:
    items = [{"id": r.id, "kind": r.definition["kind"], "domain": r.definition["domain"],
              "title": _text(r.definition["title"], locale), "title_ar_state": r.definition["title_ar_state"]}
             for r in sorted(catalog.reports.values(), key=lambda r: r.id) if r.visible_to(ctx.role)]
    return {"reports": items, "_row_count": len(items)}


def definition(catalog: Catalog, ctx: PermissionContext, report_id: str, locale: str) -> dict:
    rep = catalog.reports.get(report_id)
    if rep is None or not rep.visible_to(ctx.role):
        raise ReportNotPermitted("no such report for this role")
    d = rep.definition
    metrics, formats = {}, {}
    for code in rep.metric_codes:
        latest = catalog.metrics[code][-1]
        metrics[code] = {"name": latest.name(locale), "name_ar_state": latest.name_ar_state,
                         "aggregation": latest.aggregation, "depends_unverified": latest.depends_unverified,
                         "metric_version": latest.version, "params": latest.params}
        formats[code] = d["formats"].get(code) or DEFAULT_FORMAT.get(latest.aggregation, "decimal")
    views = [{**v, "title": _text(v.get("title"), locale)} for v in d["views"]]
    return {"id": rep.id, "kind": d["kind"], "domain": d["domain"], "title": _text(d["title"], locale),
            "title_ar_state": d["title_ar_state"], "defaults": d["defaults"], "refresh": d["refresh"],
            "print": d["print"], "source_note": _text(d["source_note"], locale),
            # Only the filters this role may use: a call taker gets no agent filter on R-04
            "filters": [f["dimension"] for f in d["filters"] if ctx.role in f["roles"]],
            "queries": d["queries"], "views": views, "formats": formats, "metrics": metrics,
            "_codes": rep.metric_codes, "_row_count": 1}
