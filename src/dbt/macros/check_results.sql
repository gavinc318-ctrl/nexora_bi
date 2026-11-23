{#- on-run-end: the results of this dbt command into meta.check_result (DD-100 item three · change 0017).

    One row per model, test, seed or snapshot dbt ran or skipped, with dbt's status and a test's row count. Hooks and
    operations are not nodes of the graph and write nothing. Commands that run nothing (parse, compile, docs, ls) are
    not recorded: their "success" says nothing about the data.

    dbt's rarer statuses are folded into the six the table knows, the original kept at the head of the message:
    runtime error and partial success -> error, no-op and reused -> skipped. Never into pass or success.

    The Airflow context comes from the environment BashOperator gives the task (AIRFLOW_CTX_*); empty by hand.
    The month's partition is made first: a missing partition would fail the hook, and with it the whole command.
    When dbt crashes before the end, this hook does not run -- the exporter sees an expected check whose last row
    is too old (DD-100 review note, item three), not a pass.
-#}
{% macro record_check_results(results) %}
    {%- if not execute or invocation_args_dict.get('which') not in ('build', 'run', 'test', 'seed', 'snapshot') -%}
        {{ return('') }}
    {%- endif -%}
    {%- set kinds = ['model', 'test', 'seed', 'snapshot'] -%}
    {%- set folded = {'runtime error': 'error', 'partial success': 'error', 'no-op': 'skipped', 'reused': 'skipped'} -%}
    {%- set rows = [] -%}
    {%- for r in results if r.node.resource_type in kinds -%}
        {%- set status = r.status | string | lower -%}
        {%- set message = (r.message or '') | string -%}
        {%- if status in folded -%}
            {%- set message = 'dbt status ' ~ status ~ ': ' ~ message -%}
            {%- set status = folded[status] -%}
        {%- endif -%}
        {%- set is_test = r.node.resource_type == 'test' -%}
        {%- set counted = is_test and status in ('pass', 'warn', 'fail') and r.failures is not none -%}
        {%- set affected = (r.adapter_response or {}).get('rows_affected') -%}
        {%- do rows.append("(" ~ [
                check_literal(invocation_id) ~ "::uuid",
                check_literal(invocation_args_dict.get('which')),
                check_literal(r.node.unique_id),
                check_literal(r.node.resource_type),
                check_literal(r.node.name),
                check_literal(status),
                (check_literal(r.node.config.severity | lower) if is_test else "null") ~ "::text",
                (r.failures | int | string if counted else "null") ~ "::bigint",
                (affected | int | string if affected is not none and not is_test else "null") ~ "::bigint",
                ("%.3f" | format(r.execution_time or 0)) ~ "::numeric",
                (check_literal(message[:500]) if message else "null") ~ "::text",
                check_literal(env_var('AIRFLOW_CTX_DAG_ID', '')) ~ "::text",
                check_literal(env_var('AIRFLOW_CTX_TASK_ID', '')) ~ "::text",
                check_literal(env_var('AIRFLOW_CTX_DAG_RUN_ID', '')) ~ "::text",
                (env_var('AIRFLOW_CTX_TRY_NUMBER', '') | int | string if env_var('AIRFLOW_CTX_TRY_NUMBER', '') else "null")
                    ~ "::integer",
            ] | join(", ") ~ ")") -%}
    {%- endfor -%}
    {%- if rows | length == 0 -%}
        {{ return('') }}
    {%- endif -%}
    select meta.ensure_monthly_partitions('meta.check_result', current_date, current_date);
    insert into meta.check_result (invocation_id, command, unique_id, resource_type, node_name, status, severity,
                                   failures, rows_affected, execution_seconds, message, dag_id, task_id, dag_run_id,
                                   try_number)
    select invocation_id, command, unique_id, resource_type, node_name, status, severity, failures, rows_affected,
           execution_seconds, message, nullif(dag_id, ''), nullif(task_id, ''), nullif(dag_run_id, ''), try_number
      from (values
        {{ rows | join(",\n        ") }}
      ) as v(invocation_id, command, unique_id, resource_type, node_name, status, severity, failures, rows_affected,
             execution_seconds, message, dag_id, task_id, dag_run_id, try_number);
    commit;
{% endmacro %}

{#- A string as a SQL literal: quotes doubled; E'' is not used, so backslashes stay as they are
    (standard_conforming_strings is on, the PostgreSQL default) -#}
{% macro check_literal(s) -%}
    '{{ (s | string).replace("'", "''") }}'
{%- endmacro %}
