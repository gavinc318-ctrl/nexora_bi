"""audit.access_log: one row per request, refused ones included (DD-99 · LD-07 chapter 7).

Written over the audit connection (the primary, DD-99 review item three) in its own transaction, after the query
has finished and before anything is returned: if the row cannot be committed the request fails (audit_unavailable)
and the result is never sent -- a query that leaves no trace is exactly what the audit is for (DD-99, rejected
alternative three). Sensitive fields are recorded by name, never by value.

Who: user_principal is the token's sub, user_display the user name (DD-99 note 2026-11-17). Every refusal of a
confirmed identity is recorded, a request that was malformed or held no single business role included -- the
latter with role_code "none"; a request's values are never recorded, only its digest. A token that does not verify
names nobody: it goes to the service log only.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .config import REPORT_TIMEZONE

# azp (the client that obtained the token) -> client_kind; anything else is recorded as api
CLIENT_KINDS = {"oss911-portal": "portal"}


@dataclass
class AccessRecord:
    subject: str
    display: str
    role: str                           # the business role, or "none" when it could not be settled
    department: str | None
    object_ref: str | None
    metric_codes: list[str] | None
    sensitive_fields: list[str] | None
    row_count: int | None
    duration_ms: int
    client: str | None
    request_digest: str | None


def write(connect, rec: AccessRecord) -> None:
    now = dt.datetime.now(ZoneInfo(REPORT_TIMEZONE))
    with connect() as conn:
        conn.execute(
            """INSERT INTO audit.access_log (date_key, user_principal, user_display, role_code, agency_type_code, action,
                                             object_ref, metric_codes, sensitive_fields, row_count, duration_ms,
                                             client_kind, request_digest)
               VALUES (%s, %s, %s, %s, %s, 'query', %s, %s, %s, %s, %s, %s, %s)""",
            (now.year * 10000 + now.month * 100 + now.day, rec.subject, rec.display, rec.role, rec.department,
             rec.object_ref, rec.metric_codes, rec.sensitive_fields, rec.row_count, rec.duration_ms,
             CLIENT_KINDS.get(rec.client or "", "api"), rec.request_digest))
        conn.commit()
