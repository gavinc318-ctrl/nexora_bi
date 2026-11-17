"""What the database says can be asked: the metric registry and the mart read rules (DD-97 · DD-99).

Both are read-only copies written by 46-metrics (metrics/metrics.yaml, metrics/marts.yaml). They are read at the
start of every request, in the request's own transaction: a deployment takes effect on the next request, and one
request never mixes two states. They are small (one row per metric version, one per mart table).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricVersion:
    code: str
    version: int
    name_zh: str
    name_en: str
    name_ar: str
    name_ar_state: str
    aggregation: str
    published: bool
    depends_unverified: bool
    effective_from: dt.date        # date.min stands for -infinity: the first version covers all history
    mart_table: str | None
    mart_column: str | None
    mart_numerator: str | None
    mart_denominator: str | None

    @property
    def implemented(self) -> bool:
        return self.published and self.mart_table is not None

    @property
    def columns(self) -> list[str]:
        """The mart columns the value is summed from: one (count, sum), or numerator and denominator (ratio, avg)."""
        if self.mart_column:
            return [self.mart_column]
        return [self.mart_numerator, self.mart_denominator]

    def name(self, locale: str) -> str:
        return self.name_ar if locale == "ar" else self.name_en


@dataclass(frozen=True)
class MartRule:
    table: str                              # schema-qualified, mart.<name>
    grain: str
    roles: frozenset[str]
    row_filter: str                         # none · department · self
    self_column: str | None
    dimensions: dict[str, str]              # dimension name -> column
    required_dimensions: frozenset[str]
    attributes: dict[str, list[str]]        # dimension name -> columns shown with a member
    sensitive_columns: dict[str, frozenset[str]] = field(default_factory=dict)   # column -> roles that may see it

    def may_see(self, role: str, column: str) -> bool:
        roles = self.sensitive_columns.get(column)
        return roles is None or role in roles


@dataclass(frozen=True)
class Catalog:
    metrics: dict[str, list[MetricVersion]]     # code -> versions, oldest first
    rules: dict[str, MartRule]                  # mart table -> rule

    @classmethod
    def load(cls, conn) -> "Catalog":
        metrics: dict[str, list[MetricVersion]] = {}
        for r in conn.execute(
                """SELECT metric_code, metric_version, name_zh, name_en, name_ar, name_ar_state, aggregation,
                          is_published, depends_unverified,
                          CASE WHEN effective_from = '-infinity' THEN NULL ELSE effective_from END,
                          mart_table, mart_column, mart_numerator, mart_denominator
                     FROM meta.metric_registry ORDER BY metric_code, metric_version"""):
            metrics.setdefault(r[0], []).append(MetricVersion(
                code=r[0], version=r[1], name_zh=r[2], name_en=r[3], name_ar=r[4], name_ar_state=r[5],
                aggregation=r[6], published=r[7], depends_unverified=r[8], effective_from=r[9] or dt.date.min,
                mart_table=r[10], mart_column=r[11], mart_numerator=r[12], mart_denominator=r[13]))
        rules = {}
        for r in conn.execute(
                """SELECT mart_table, grain, roles, row_filter, self_column, dimensions, required_dimensions,
                          attributes, sensitive_columns FROM meta.mart_rule"""):
            rules[r[0]] = MartRule(
                table=r[0], grain=r[1], roles=frozenset(r[2]), row_filter=r[3], self_column=r[4],
                dimensions=dict(r[5]), required_dimensions=frozenset(r[6]), attributes=dict(r[7]),
                sensitive_columns={k: frozenset(v) for k, v in r[8].items()})
        return cls(metrics=metrics, rules=rules)
