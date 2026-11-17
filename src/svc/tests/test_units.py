"""Tests without a database: tokens, the cache key, the department filter, metric versions over a range."""

from __future__ import annotations

import datetime as dt

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from oss911_svc.auth import Principal
from oss911_svc.cache_key import cache_key
from oss911_svc.catalog import Catalog, MartRule, MetricVersion
from oss911_svc.context import PermissionContext
from oss911_svc.errors import (DepartmentUnmapped, FieldNotPermitted, IdentityUnmapped, MetricNotImplemented,
                               MetricVersionBoundary, MetricVersionUnknown, RoleAmbiguous, RoleMissing,
                               TableNotPermitted, Unauthenticated)
from oss911_svc.query import MetricRef, QueryRequest, check_request, plan_metrics


# ---------------------------------------------------------------- tokens
def test_token_gives_the_subject_the_role_and_the_claims(verifier, token):
    p = verifier.principal(token(user="u", roles=("supervisor", "offline_access"), agent_no="101",
                                 agency_type_code="POL"))
    assert (p.subject, p.display, p.role, p.agent_no, p.department_claim, p.client) == (
        "id-u", "u", "supervisor", "101", "POL", "oss911-portal")


def test_subject_is_the_key_not_the_user_name(verifier, token):          # DD-99 note 2026-11-17
    renamed = verifier.principal(token(user="new-name", sub="id-u"))
    assert (renamed.subject, renamed.display) == ("id-u", "new-name")
    with pytest.raises(Unauthenticated):
        verifier.principal(token(sub=None))


@pytest.mark.parametrize("change", [
    {"aud": "another-client"}, {"iss": "http://elsewhere/realms/oss911"}, {"ttl": -60},
])
def test_token_refused(verifier, token, change):
    with pytest.raises(Unauthenticated):
        verifier.principal(token(**change))


def test_token_signed_by_another_key_refused(verifier, token):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(Unauthenticated):
        verifier.principal(token(signing_key=other))


def test_no_token_no_role_and_two_roles(verifier, token):
    with pytest.raises(Unauthenticated):
        verifier.identify(None)
    identity = verifier.identify(token(roles=("offline_access",)))      # identified: the refusal is audited
    with pytest.raises(RoleMissing):
        identity.principal()
    with pytest.raises(RoleAmbiguous):                                    # scopes are not nested: no "broadest"
        verifier.identify(token(roles=("call_taker", "dispatcher"))).principal()


# ---------------------------------------------------------------- cache key (LD-04 3.3)
BASE = dict(request_digest="r", metric_versions={"M-C04": [1]}, publication_seq=10, role="dispatcher",
            visible_sensitive=[], row_scope="department:POL")


def test_cache_key_differs_by_department():
    assert cache_key(**BASE) != cache_key(**{**BASE, "row_scope": "department:EMS"})


def test_cache_key_differs_by_publication():
    assert cache_key(**BASE) != cache_key(**{**BASE, "publication_seq": 11})


def test_cache_key_differs_between_two_call_takers_of_one_department():
    a = {**BASE, "role": "call_taker", "row_scope": "self:101"}
    assert cache_key(**a) != cache_key(**{**a, "row_scope": "self:102"})


def test_cache_key_differs_by_visible_fields_and_role():
    assert cache_key(**BASE) != cache_key(**{**BASE, "visible_sensitive": ["agent_name_en"]})
    assert cache_key(**BASE) != cache_key(**{**BASE, "role": "supervisor"})


def test_cache_key_stable():
    assert cache_key(**BASE) == cache_key(**dict(BASE))


# ---------------------------------------------------------------- permission context
DEPT_RULE = MartRule(table="mart.dispatch__day__orders", grain="day x department",
                     roles=frozenset({"dispatcher", "supervisor", "call_taker", "admin"}),
                     row_filter="department", self_column=None,
                     dimensions={"date": "date_key", "agency_type": "agency_type_code"},
                     required_dimensions=frozenset(), attributes={})
SELF_RULE = MartRule(table="mart.legs__day_agent__handling", grain="day x agent",
                     roles=frozenset({"call_taker", "supervisor", "admin"}), row_filter="self", self_column="agent_no",
                     dimensions={"date": "date_key", "agent": "agent_no"}, required_dimensions=frozenset(),
                     attributes={"agent": ["agent_name_en"]},
                     sensitive_columns={"agent_name_en": frozenset({"supervisor", "admin"})})


class FakeConn:
    """Answers the three lookups PermissionContext.resolve makes."""

    def __init__(self, mapped=None, valid=(), agents=None):
        self.mapped, self.valid, self.agents = mapped or {}, set(valid), agents or {}

    def execute(self, query, params):
        value = params[0]

        class R:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row
        if "user_department_map" in query:
            return R((self.mapped[value],) if value in self.mapped else None)
        if "dim_agency_type" in query:
            return R((value in self.valid,))
        return R((self.agents.get(value, 0),))


def ctx(role="dispatcher", dept=None, agent=None, **conn):
    return PermissionContext.resolve(FakeConn(**conn), Principal("id-u", "u", role, agent, dept, None))


def test_department_unmapped_is_refused_not_empty():          # TC-DISC-03 on the mechanism
    with pytest.raises(DepartmentUnmapped):
        ctx().row_filter(DEPT_RULE)
    with pytest.raises(DepartmentUnmapped):                      # in the token, but not a current code
        ctx(dept="XXX", valid={"POL"}).row_filter(DEPT_RULE)


def test_department_from_the_map_when_not_in_the_token():
    c = ctx(mapped={"id-u": "POL"}, valid={"POL"})                    # the map is keyed by subject
    pred, params = c.row_filter(DEPT_RULE)
    assert params == ["POL"] and c.scope(DEPT_RULE) == "department:POL"


def test_department_missing_does_not_block_a_table_without_that_filter():   # review item three
    pred, params = ctx(role="call_taker", agent="101", agents={"101": 1}).row_filter(SELF_RULE)
    assert params == ["101"]


@pytest.mark.parametrize("role,filtered", [("dispatcher", True), ("supervisor", True), ("call_taker", False),
                                           ("admin", False)])
def test_department_filter_only_for_dispatchers_and_supervisors(role, filtered):   # LD-07 2.1
    c = ctx(role=role, dept="POL", valid={"POL"})
    pred, params = c.row_filter(DEPT_RULE)
    assert params == (["POL"] if filtered else [])
    assert c.scope(DEPT_RULE) == ("department:POL" if filtered else "all")
    if not filtered:
        ctx(role=role).row_filter(DEPT_RULE)                              # no department needed
        assert not [p for p in ctx(role=role).problems() if "department" in p]


def test_department_filter_reads_the_agency_type_dimension():
    pred, _ = ctx(dept="POL", valid={"POL"}).row_filter(DEPT_RULE)
    assert "agency_type_code" in pred.as_string(None)


@pytest.mark.parametrize("agent,agents", [(None, {}), ("999", {}), ("101", {"101": 2})])
def test_self_filter_refuses_unmapped_identity(agent, agents):
    with pytest.raises(IdentityUnmapped):
        ctx(role="call_taker", agent=agent, agents=agents).row_filter(SELF_RULE)


def test_supervisor_not_limited_to_self():
    assert ctx(role="supervisor").row_filter(SELF_RULE)[1] == []


def test_fields_and_tables():
    with pytest.raises(FieldNotPermitted):
        ctx(role="call_taker").check_fields(SELF_RULE, {"agent_name_en": "agent_name_en"})
    ctx(role="supervisor").check_fields(SELF_RULE, {"agent_name_en": "agent_name_en"})
    with pytest.raises(TableNotPermitted):
        ctx(role="dispatcher").check_table(SELF_RULE)


# ---------------------------------------------------------------- metric versions over a range (review item four)
def mv(version, start, column, implemented=True):
    return MetricVersion(code="M-X01", version=version, name_zh="x", name_en="x", name_ar="x", name_ar_state="draft",
                         aggregation="count", published=True, depends_unverified=False, effective_from=start,
                         mart_table="mart.legs__day_agent__handling" if implemented else None,
                         mart_column=column, mart_numerator=None, mart_denominator=None)


CATALOG = Catalog(metrics={"M-X01": [mv(1, dt.date.min, "n"), mv(2, dt.date(2026, 11, 10), "n_v2")],
                           "M-X02": [mv(1, dt.date.min, None, implemented=False)]},
                  rules={SELF_RULE.table: SELF_RULE})


def request(**kw):
    base = dict(metrics=[MetricRef("M-X01")], range_from=dt.date(2026, 11, 1), range_to=dt.date(2026, 11, 20))
    return QueryRequest(**{**base, **kw})


def test_range_across_a_new_definition():
    sup = ctx(role="supervisor")
    req = request(dimensions=["date"], granularity="day")
    plan, rule = plan_metrics(CATALOG, req)
    assert [(s.version.version, s.start, s.end) for s in plan["M-X01"]] == [
        (1, dt.date(2026, 11, 1), dt.date(2026, 11, 10)), (2, dt.date(2026, 11, 10), dt.date(2026, 11, 20))]
    check_request(req, plan, rule, sup)                          # by day: allowed, each row says its version
    for bad in (request(dimensions=["date"], granularity="week"), request(dimensions=[])):
        with pytest.raises(MetricVersionBoundary):
            check_request(bad, *plan_metrics(CATALOG, bad), sup)


def test_named_version_and_unknown_version():
    req = request(metrics=[MetricRef("M-X01", 2)], granularity="month", dimensions=["date"])
    plan, rule = plan_metrics(CATALOG, req)
    assert [s.version.version for s in plan["M-X01"]] == [2]
    check_request(req, plan, rule, ctx(role="supervisor"))
    with pytest.raises(MetricVersionUnknown):
        plan_metrics(CATALOG, request(metrics=[MetricRef("M-X01", 7)]))
    with pytest.raises(MetricNotImplemented):
        plan_metrics(CATALOG, request(metrics=[MetricRef("M-X02")]))
