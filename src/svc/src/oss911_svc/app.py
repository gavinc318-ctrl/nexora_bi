"""The query API (DD-99 · LD-04 3.5): four endpoints under /v1.

    POST /v1/query                          metrics by dimensions over a date range
    GET  /v1/metrics                        the metric dictionary: names, versions, implemented, readable by me
    GET  /v1/dimensions/{dimension}/members the members of a dimension of a metric's table, in my scope
    GET  /v1/me                             what the service made of my token: role, department, agent, scopes

Every request: verify the token -> open the analytic connection (read only, one transaction) -> read the
registry and the read rules -> resolve the permission context once -> do the work, the result kept in memory ->
write audit.access_log over the audit connection and commit -> only then respond. A refusal is audited too.

Run by systemd as: uvicorn --factory oss911_svc.app:serve (settings from /etc/oss911, config.py).
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Callable, Literal, Union
from zoneinfo import ZoneInfo

import psycopg
from fastapi import FastAPI, Header, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__, audit
from .auth import Principal, TokenVerifier
from .cache_key import cache_key
from .catalog import Catalog
from .config import REPORT_TIMEZONE, Settings
from .context import PermissionContext
from .errors import ALL as ALL_ERRORS
from .errors import ApiError, AuditUnavailable, RequestInvalid
from .query import MetricRef, QueryRequest, run_members, run_query

log = logging.getLogger("oss911_svc")


# ---------------------------------------------------------------- request and response models (the OpenAPI)
class MetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(examples=["M-C04"])
    metric_version: int | None = Field(None, description="Omitted: each day uses the version in effect that day")


class DateRange(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: dt.date = Field(alias="from", description="First day, inclusive (Asia/Riyadh calendar day)")
    to: dt.date = Field(description="Day after the last, exclusive")


class QueryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[Union[str, MetricSpec]] = Field(min_length=1, description='"M-C04" or {"code", "metric_version"}; '
                                                  "all from the same mart table")
    dimensions: list[str] = Field(default_factory=list, description='Output dimensions; "date" groups by granularity')
    filters: dict[str, list[str]] = Field(default_factory=dict, description="Dimension -> members to keep")
    range: DateRange
    granularity: Literal["day", "week", "month", "quarter", "year"] = "day"
    data_version: Union[Literal["latest", "as_closed"], int] = Field(
        "latest", description="latest: newest version per day, provisional included; as_closed: close_version 1 "
                              "only; a number: that close_version")
    locale: Literal["ar", "en"] = "en"


class DayState(BaseModel):
    date: dt.date
    status: Literal["published", "not_built", "not_closed", "version_not_found"]
    close_version: int | None = None
    publication_seq: int | None = None


class MetricVersionSpan(BaseModel):
    metric_version: int
    from_: dt.date = Field(alias="from")
    to: dt.date


class QueryMetricMeta(BaseModel):
    code: str
    name: str
    aggregation: str
    depends_unverified: bool
    versions: list[MetricVersionSpan]


class Availability(BaseModel):
    metric: str
    year: int
    state: str
    limiting_column: str | None


class QueryMeta(BaseModel):
    table: str
    data_version: Union[str, int]
    metrics: list[QueryMetricMeta]
    publication_seq: int | None = Field(description="Highest publication read; changes when a correction is published")
    data_as_of: dt.datetime | None
    days: list[DayState]
    unclosed_dates: list[dt.date]
    in_progress_calls: int | None = Field(description="Calls not complete yet, on the provisional days read, in my scope")
    has_unlinked: bool | None = Field(description="Rows under an 'unlinked' agent or queue were read (DD-95)")
    empty_reason: Literal["no_data", "out_of_availability"] | None
    availability: list[Availability]
    row_scope: str
    cache_key: str = Field(description="The key a result cache would use: request, data and permission context "
                                       "(LD-04 3.3); no cache in the first version")


class QueryResponse(BaseModel):
    rows: list[dict[str, Any]] = Field(description="One per output row: the dimensions, then each metric's value; "
                                                   "metric_versions when the range crosses a new definition")
    meta: QueryMeta


class MetricVersionInfo(BaseModel):
    metric_version: int
    effective_from: dt.date | None = Field(description="None: from the beginning")
    implemented: bool
    mart_table: str | None


class MetricInfo(BaseModel):
    code: str
    name: str
    name_ar_state: str
    aggregation: str
    depends_unverified: bool
    readable: bool = Field(description="Implemented, and my role may read its table")
    dimensions: list[str]
    versions: list[MetricVersionInfo]


class MetricsResponse(BaseModel):
    metrics: list[MetricInfo]


class MembersResponse(BaseModel):
    members: list[dict[str, Any]]
    meta: dict[str, Any]


class TableAccess(BaseModel):
    table: str
    readable: bool
    row_filter: str
    row_scope: str | None = Field(description="None: the filter cannot be applied for me (see problems)")
    visible_sensitive_fields: list[str]


class MeResponse(BaseModel):
    user: str
    role: str
    agent_no: str | None
    department: str | None
    problems: list[str]
    tables: list[TableAccess]


class ErrorBody(BaseModel):
    error: Literal[tuple(sorted({e.code for e in ALL_ERRORS} | {"internal_error"}))]   # type: ignore[valid-type]
    message: str
    detail: dict[str, Any] | None = None


ERROR_RESPONSES = {s: {"model": ErrorBody} for s in sorted({e.status for e in ALL_ERRORS} | {500})}


# ---------------------------------------------------------------- the application
def create_app(settings: Settings | None = None, verifier: TokenVerifier | None = None,
               connect_analytic: Callable | None = None, connect_audit: Callable | None = None) -> FastAPI:
    """settings=None builds the application for generating the OpenAPI only; tests pass their own connections."""
    app = FastAPI(title="OSS911 BI query API", version=__version__,
                  description="Metrics by dimensions over the published mart tables (DD-99 · LD-04 3.5). "
                              "Every response or refusal is audited; refusals carry their own error code.")

    if settings is not None:
        verifier = verifier or TokenVerifier.from_jwks(settings.oidc_issuer, settings.oidc_audience, settings.jwks_url)

        def _connect(dsn):
            return lambda: psycopg.connect(dsn, user=settings.db_user, password=settings.db_password,
                                           prepare_threshold=None,    # pgBouncer transaction pool (DD-67)
                                           application_name="oss911-svc", connect_timeout=5)
        connect_analytic = connect_analytic or _connect(settings.analytic_dsn)
        connect_audit = connect_audit or _connect(settings.audit_dsn)

    def handle(authorization: str | None, work: Callable, object_ref: str | None,
               metric_codes: list[str] | None, digest: str | None):
        started = time.monotonic()
        principal: Principal | None = None
        ctx: PermissionContext | None = None
        result, error = None, None
        try:
            principal = verifier.principal(authorization)
            with connect_analytic() as conn:
                conn.execute("SET TRANSACTION READ ONLY")
                catalog = Catalog.load(conn)
                ctx = PermissionContext.resolve(conn, principal)
                result = work(conn, catalog, ctx)
                conn.rollback()
        except ApiError as exc:
            error = exc
        except Exception:   # noqa: BLE001 -- logged with its traceback, answered without internals
            log.exception("request failed")
            error = ApiError("internal error")
            error.status, error.code = 500, "internal_error"

        if principal is not None:
            rec = audit.AccessRecord(
                user=principal.user, role=principal.role, department=ctx.department if ctx else None,
                object_ref=(result or {}).get("_table") or object_ref, metric_codes=metric_codes,
                sensitive_fields=(result or {}).get("_sensitive") or None,
                row_count=len(result.get("rows", result.get("members", result.get("metrics", [])))) if result else None,
                duration_ms=int((time.monotonic() - started) * 1000), client=principal.client, request_digest=digest)
            try:
                audit.write(connect_audit, rec)
            except Exception:   # noqa: BLE001
                log.exception("audit write failed")
                error, result = AuditUnavailable("the access could not be audited, so nothing is returned"), None
        else:
            log.warning("unauthenticated request refused: %s", error.message if error else "")

        if error is not None:
            return JSONResponse(status_code=error.status, content=error.body())
        return {k: v for k, v in result.items() if not k.startswith("_")}

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        problems = [{"field": ".".join(str(p) for p in e.get("loc", [])), "problem": e.get("msg")} for e in exc.errors()]
        return JSONResponse(status_code=400, content=RequestInvalid("the request is malformed",
                                                                    problems=problems).body())

    @app.post("/v1/query", response_model=QueryResponse, responses=ERROR_RESPONSES, tags=["query"])
    def query(body: QueryBody, authorization: str | None = Header(None)):
        req = QueryRequest(
            metrics=[MetricRef(m) if isinstance(m, str) else MetricRef(m.code, m.metric_version) for m in body.metrics],
            range_from=body.range.from_, range_to=body.range.to, dimensions=body.dimensions, filters=body.filters,
            granularity=body.granularity, data_version=body.data_version, locale=body.locale)

        def work(conn, catalog, ctx):
            out = run_query(conn, catalog, ctx, req)
            rule = catalog.rules[out["_table"]]
            out["meta"]["cache_key"] = cache_key(
                request_digest=req.digest(),
                metric_versions={m["code"]: [v["metric_version"] for v in m["versions"]] for m in out["meta"]["metrics"]},
                publication_seq=out["meta"]["publication_seq"], role=ctx.role,
                visible_sensitive=ctx.visible_sensitive(rule), row_scope=ctx.scope(rule))
            return out
        return handle(authorization, work, "/v1/query", [m.code for m in req.metrics], req.digest())

    @app.get("/v1/metrics", response_model=MetricsResponse, responses=ERROR_RESPONSES, tags=["dictionary"])
    def metrics(locale: Literal["ar", "en"] = "en", authorization: str | None = Header(None)):
        def work(conn, catalog, ctx):
            items = []
            for code, versions in sorted(catalog.metrics.items()):
                latest = versions[-1]
                rule = catalog.rules.get(latest.mart_table or "")
                items.append({
                    "code": code, "name": latest.name(locale), "name_ar_state": latest.name_ar_state,
                    "aggregation": latest.aggregation, "depends_unverified": latest.depends_unverified,
                    "readable": bool(latest.implemented and rule and ctx.role in rule.roles),
                    "dimensions": sorted(rule.dimensions) if rule else [],
                    "versions": [{"metric_version": v.version,
                                  "effective_from": None if v.effective_from == dt.date.min else v.effective_from,
                                  "implemented": v.implemented, "mart_table": v.mart_table} for v in versions]})
            return {"metrics": items}
        return handle(authorization, work, "/v1/metrics", None, None)

    @app.get("/v1/dimensions/{dimension}/members", response_model=MembersResponse, responses=ERROR_RESPONSES,
             tags=["dictionary"])
    def members(dimension: str = Path(description="A dimension of the metric's table"),
                metric: str = Query(description="The metric whose table the members come from"),
                from_: dt.date | None = Query(None, alias="from", description="Default: 31 days before to"),
                to: dt.date | None = Query(None, description="Exclusive; default: tomorrow (Asia/Riyadh)"),
                attributes: list[str] = Query(default_factory=list, description="Attributes to return with each member"),
                authorization: str | None = Header(None)):
        end = to or (dt.datetime.now(ZoneInfo(REPORT_TIMEZONE)).date() + dt.timedelta(days=1))
        start = from_ or (end - dt.timedelta(days=31))

        def work(conn, catalog, ctx):
            if end <= start:
                raise RequestInvalid("to must be after from")
            return run_members(conn, catalog, ctx, metric, dimension, attributes, start, end)
        return handle(authorization, work, f"/v1/dimensions/{dimension}/members", [metric], None)

    @app.get("/v1/me", response_model=MeResponse, responses=ERROR_RESPONSES, tags=["dictionary"])
    def me(authorization: str | None = Header(None)):
        def work(conn, catalog, ctx):
            tables = []
            for name, rule in sorted(catalog.rules.items()):
                try:
                    ctx.row_filter(rule)
                    scope = ctx.scope(rule)
                except ApiError:
                    scope = None
                tables.append({"table": name, "readable": ctx.role in rule.roles, "row_filter": rule.row_filter,
                               "row_scope": scope if ctx.role in rule.roles else None,
                               "visible_sensitive_fields": ctx.visible_sensitive(rule)})
            problems = [p for p in (ctx.identity_problem, ctx.department_problem) if p]
            return {"user": ctx.principal.user, "role": ctx.role, "agent_no": ctx.agent_no,
                    "department": ctx.department, "problems": problems, "tables": tables}
        return handle(authorization, work, "/v1/me", None, None)

    return app


def serve() -> FastAPI:
    """The factory systemd runs (uvicorn --factory)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app(Settings.load())
