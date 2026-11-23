{#- Daily maintenance (DD-77 · DD-91 item 11): monthly partitions for this month and the next N, for every
    partitioned table in core, audit, raw and meta (meta.check_result, change 0017). A missing partition makes every
    write for that month fail, so this runs every day; when it fails, the DAG task fails and alerts. Writes of older
    months create their own partitions first (the pre-hook of core models).

    dbt run-operation ensure_partitions_ahead --args '{months: 3}'
-#}
{% macro ensure_partitions_ahead(months=3) %}
    {% set tables = run_query("
        select n.nspname || '.' || c.relname
          from pg_partitioned_table pt
          join pg_class c on c.oid = pt.partrelid
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname in ('core', 'audit', 'raw', 'meta')
         order by 1") %}
    {% set created = [] %}
    {% for row in tables.rows %}
        {% set r = run_query("select meta.ensure_monthly_partitions('" ~ row[0] ~ "', current_date, (current_date + interval '"
                              ~ months ~ " months')::date)") %}
        {% do created.append(r.rows[0][0]) %}
    {% endfor %}
    {% do run_query("commit") %}
    {{ log("partitions: " ~ tables.rows | length ~ " partitioned tables, " ~ created | sum ~ " partitions created, "
           ~ "covering this month and " ~ months ~ " ahead", info=true) }}
{% endmacro %}

{#- Daily maintenance: the retention of meta.check_result (DD-100 item 7, change 0017). Whole monthly partitions whose
    month ended more than var check_result_keep_days ago are dropped; the function refuses fewer than 90 days.

    dbt run-operation drop_expired_check_results
-#}
{% macro drop_expired_check_results() %}
    {% set r = run_query("select meta.drop_expired_check_partitions(" ~ var('check_result_keep_days') | int ~ ")") %}
    {% do run_query("commit") %}
    {{ log("check_result: " ~ r.rows[0][0] ~ " partitions past " ~ var('check_result_keep_days') ~ " days dropped",
           info=true) }}
{% endmacro %}
