"""The query API's errors (DD-99 · LD-04 10.1): each its own code, because "no data" is always zero in a number and
can come from different causes the user cannot tell apart. A response is either a result or one of these -- never
an empty result standing in for a refusal."""

from __future__ import annotations


class ApiError(Exception):
    status = 400
    code = "request_invalid"

    def __init__(self, message: str, **detail):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def body(self) -> dict:
        return {"error": self.code, "message": self.message, **({"detail": self.detail} if self.detail else {})}


def _error(name: str, status: int, code: str) -> type[ApiError]:
    return type(name, (ApiError,), {"status": status, "code": code})


RequestInvalid = _error("RequestInvalid", 400, "request_invalid")
Unauthenticated = _error("Unauthenticated", 401, "unauthenticated")
RoleMissing = _error("RoleMissing", 403, "role_missing")
TableNotPermitted = _error("TableNotPermitted", 403, "table_not_permitted")
FieldNotPermitted = _error("FieldNotPermitted", 403, "field_not_permitted")
DepartmentUnmapped = _error("DepartmentUnmapped", 403, "department_unmapped")
IdentityUnmapped = _error("IdentityUnmapped", 403, "identity_unmapped")
MetricUnknown = _error("MetricUnknown", 404, "metric_unknown")
MetricVersionUnknown = _error("MetricVersionUnknown", 404, "metric_version_unknown")
MetricNotImplemented = _error("MetricNotImplemented", 404, "metric_not_implemented")
DimensionInvalid = _error("DimensionInvalid", 400, "dimension_invalid")
IncompatibleGrain = _error("IncompatibleGrain", 400, "incompatible_grain")
MetricVersionBoundary = _error("MetricVersionBoundary", 400, "metric_version_boundary")
ResultTooLarge = _error("ResultTooLarge", 400, "result_too_large")
Timeout = _error("Timeout", 504, "timeout")
AuditUnavailable = _error("AuditUnavailable", 500, "audit_unavailable")

ALL = [RequestInvalid, Unauthenticated, RoleMissing, TableNotPermitted, FieldNotPermitted, DepartmentUnmapped,
       IdentityUnmapped, MetricUnknown, MetricVersionUnknown, MetricNotImplemented, DimensionInvalid,
       IncompatibleGrain, MetricVersionBoundary, ResultTooLarge, Timeout, AuditUnavailable]
