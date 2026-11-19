{#- Casts that return NULL instead of failing (run at the start of every dbt invocation, on-run-start).

    Ingestion quarantines a batch with an unparseable time or integer (cdr.py), so such values normally never reach
    staging. A quarantined batch can still be released (M-03, with a recorded reason); then a single bad value must
    not stop the whole model -- the value becomes NULL, and the row is counted as unmapped (DD-06), not dropped.
    The functions live in schema staging, owned by oss911_transform; nothing outside dbt uses them.
-#}
{% macro create_safe_casts() -%}
    create or replace function staging.try_int(p text) returns integer
    language plpgsql immutable set search_path = pg_catalog as $f$
    begin
        if p is null or p !~ '^-?[0-9]{1,9}$' then return null; end if;
        return p::integer;
    end $f$;

    create or replace function staging.try_timestamp(p text) returns timestamp
    language plpgsql immutable set search_path = pg_catalog as $f$
    begin
        if p is null or p !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$' then return null; end if;
        return p::timestamp;               -- still fails on 2026-02-30: caught below
    exception when others then
        return null;
    end $f$;

    -- An integer written "n" or "n.0" (ICP's historical indexes send "10759.0" for whole seconds, DD-98 item four)
    create or replace function staging.try_whole(p text) returns integer
    language plpgsql immutable set search_path = pg_catalog as $f$
    begin
        if p is null or p !~ '^-?[0-9]{1,9}(\.0+)?$' then return null; end if;
        return split_part(p, '.', 1)::integer;
    end $f$;
{%- endmacro %}
