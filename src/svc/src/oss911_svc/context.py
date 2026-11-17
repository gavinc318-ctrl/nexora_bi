"""The permission context: resolved once per request, used by every path that returns data (DD-99 review item one).

The main query, the dimension members and the response's meta fields (in_progress_calls, has_unlinked) all take
their row filter and field checks from here, so the three cannot drift apart.

Row filters (LD-07 2.3 · DD-99 review items two, three), applied as the table's rule says:
  self        call takers see only the rows of their own agent number: the token's agent_no, matched on the
              table's self_column, which covers every dimension version of that agent. Missing from the token,
              matching no agent, or matching more than one (the same number at two sites) -> identity_unmapped;
              the query never runs without the filter. Supervisors and administrators are not limited by it.
  department  the user's current department: the token's agency_type_code, else meta.user_department_map (IR-39);
              it must be a current code of core.dim_agency_type. Otherwise department_unmapped -- never an empty
              result. Applied to every role but admin, on the column of the table's dimension "department".
  none        no row filter; a missing department or agent number does not block it (review item three)
Fields: a sensitive column (its rule lists the roles that may see it) may not be output, filtered on or requested
by anyone else -- the whole request is refused (field_not_permitted), never silently cut.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import sql

from .auth import Principal
from .catalog import MartRule
from .errors import DepartmentUnmapped, FieldNotPermitted, IdentityUnmapped, TableNotPermitted

ROLES_WITHOUT_SELF_FILTER = {"supervisor", "admin"}
ROLES_WITHOUT_DEPARTMENT_FILTER = {"admin"}


@dataclass(frozen=True)
class PermissionContext:
    principal: Principal
    department: str | None              # resolved and valid, else None
    department_problem: str | None      # why there is none
    agent_no: str | None                # resolved to exactly one agent, else None
    identity_problem: str | None        # why there is none

    @property
    def role(self) -> str:
        return self.principal.role

    @classmethod
    def resolve(cls, conn, principal: Principal) -> "PermissionContext":
        dept, dept_problem = principal.department_claim, None
        if dept is None:
            row = conn.execute("SELECT agency_type_code FROM meta.user_department_map WHERE user_principal = %s",
                               (principal.user,)).fetchone()
            dept = row[0] if row else None
        if dept is None:
            dept_problem = "no department in the token and none mapped in meta.user_department_map"
        elif not conn.execute("SELECT EXISTS (SELECT 1 FROM core.dim_agency_type WHERE code = %s AND is_current)",
                              (dept,)).fetchone()[0]:
            dept_problem = f"department {dept} is not a current agency type code"
            dept = None

        agent, identity_problem = principal.agent_no, None
        if agent is None:
            identity_problem = "the token carries no agent_no"
        else:
            n = conn.execute("""SELECT count(DISTINCT (site_sk, src_natural_key)) FROM core.dim_agent
                                 WHERE src_natural_key = %s""", (agent,)).fetchone()[0]
            if n != 1:
                identity_problem = (f"agent number {agent} matches no agent" if n == 0
                                    else f"agent number {agent} matches {n} agents")
                agent = None
        return cls(principal=principal, department=dept, department_problem=dept_problem,
                   agent_no=agent, identity_problem=identity_problem)

    # ---- tables and fields ----
    def check_table(self, rule: MartRule) -> None:
        if self.role not in rule.roles:
            raise TableNotPermitted(f"role {self.role} may not read {rule.table}", table=rule.table)

    def check_fields(self, rule: MartRule, fields: dict[str, str]) -> None:
        """fields: what the request names (a dimension or attribute name) -> its column."""
        refused = sorted(name for name, column in fields.items() if not rule.may_see(self.role, column))
        if refused:
            raise FieldNotPermitted(f"role {self.role} may not see {', '.join(refused)}", fields=refused)

    def visible_sensitive(self, rule: MartRule) -> list[str]:
        return sorted(c for c in rule.sensitive_columns if rule.may_see(self.role, c))

    # ---- rows ----
    def row_filter(self, rule: MartRule, alias: str = "t") -> tuple[sql.Composable, list]:
        """The predicate this user's rows must meet in the table, or a refusal. Never an absent filter where one
        applies."""
        if rule.row_filter == "self" and self.role not in ROLES_WITHOUT_SELF_FILTER:
            if self.agent_no is None:
                raise IdentityUnmapped(f"cannot limit {rule.table} to your own rows: {self.identity_problem}")
            return sql.SQL("{} = %s").format(sql.Identifier(alias, rule.self_column)), [self.agent_no]
        if rule.row_filter == "department" and self.role not in ROLES_WITHOUT_DEPARTMENT_FILTER:
            if self.department is None:
                raise DepartmentUnmapped(f"cannot limit {rule.table} to your department: {self.department_problem}")
            column = rule.dimensions.get("department")
            if column is None:
                raise TableNotPermitted(f"{rule.table} declares a department filter but no department dimension")
            return sql.SQL("{}::text = %s").format(sql.Identifier(alias, column)), [self.department]
        return sql.SQL("true"), []

    def scope(self, rule: MartRule) -> str:
        """The row scope in force, for the cache key and /me: who these rows were limited to."""
        if rule.row_filter == "self" and self.role not in ROLES_WITHOUT_SELF_FILTER:
            return f"self:{self.agent_no}"
        if rule.row_filter == "department" and self.role not in ROLES_WITHOUT_DEPARTMENT_FILTER:
            return f"department:{self.department}"
        return "all"
