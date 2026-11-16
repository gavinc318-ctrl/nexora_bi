{#- Publishing mart tables by day, with close versions (DD-81 · DD-97 2026-11-16 items six to nine · DD-99).

    A mart model (models/mart/*.sql) publishes one view (models/mart/calc, schema staging) that computes the
    table's rows for every day from core. A run writes whole days, never part of one:

      var mart_kind = intraday (hourly)
        every day not closed yet, from the first open day (at most a week back) to today, is rewritten as
        close_version 0, "provisional": its rows are deleted and written again each time (item six)
      var mart_kind = nightly (daily)
        closing: the open days whose data is final, in date order up to the first one that is not, are written as
        close_version 1 and their provisional rows deleted. Final = the day's re-sweep has reached core: every
        source day (in the source's time zone) the report day overlaps was re-swept by a successful run whose
        batches were all consumed by fact_call_leg -- or discarded in quarantine: a person decided, no data will
        come (Mac side 2026-11-16); a batch still pending holds the day. Source days before the first re-sweep ever
        made count as final -- no re-sweep will come for them (item seven). A day is never closed past an open one: the first
        open day is "the day after the last closed one", so a gap would be skipped for good. Calls still
        incomplete then are not counted (they show in incomplete_calls)
        correction: each of the last 7 closed days is recomputed and compared with its latest version, row by
        row; a difference writes the whole day again as close_version + 1, the earlier rows stay (item eight).
        Changes further back are not corrected automatically
      Each kind written is one meta.data_publication row; each day written is one meta.mart_day_publication row,
      zero rows included (DD-97 review). Every mart row carries close_version and the publication_seq that wrote it.

    The model, its hooks and its insert run in one transaction; the steps share temporary tables dropped at
    commit: mart_closed (latest closed version per day), mart_days (days to write: kind, version, publication),
    mart_cand (their rows from the view). -#}


{#- The days of days_relation (a CTE with date_key) whose source data is final; see the header. source_id: the
    source whose re-sweep decides it; its contract gives the time zone. -#}
{% macro mart_final_days(source_id, days_relation) -%}
    with tz as (
        select definition #>> '{window,source_timezone}' as src_tz
          from meta.source_contract where source_id = '{{ source_id }}'
    ),
    sweep_runs as (           -- one re-sweep run covers one source day; its last batch ends at day + 1, 00:00
        select b.run_id,
               (max(b.window_to)::timestamp - interval '1 day')::date                   as source_day,
               bool_and(exists (select 1 from meta.staging_batch_state s
                                 where s.model = 'fact_call_leg' and s.run_id = b.run_id
                                   and s.batch_seq = b.batch_seq)
                        or exists (select 1 from meta.quarantine_batch q
                                    where q.run_id = b.run_id and q.batch_seq = b.batch_seq
                                      and q.state = 'discarded'))                     as in_core
          from meta.ingest_batch b
          join meta.run_log r on r.run_id = b.run_id and r.outcome = 'success'
         where b.source_id = '{{ source_id }}' and b.mode = 'resweep'
         group by b.run_id
    ),
    swept as (
        select distinct source_day from sweep_runs where in_core
    )
    select o.date_key
      from {{ days_relation }} o, tz
     where exists (select 1 from swept)
       and not exists (
         select 1
           from generate_series(
                    ((to_date(o.date_key::text, 'YYYYMMDD')::timestamp
                      at time zone '{{ var("report_timezone") }}') at time zone tz.src_tz)::date,
                    (((to_date(o.date_key::text, 'YYYYMMDD') + 1)::timestamp
                      at time zone '{{ var("report_timezone") }}' - interval '1 microsecond')
                      at time zone tz.src_tz)::date,
                    interval '1 day') sd
          where sd::date >= (select min(source_day) from swept)
            and sd::date not in (select source_day from swept))
{%- endmacro %}


{#- The calc view's columns, in order (the mart table is these plus close_version and publication_seq) -#}
{% macro mart_columns(calc) -%}
    {%- if execute -%}
        {%- for c in adapter.get_columns_in_relation(calc) -%}{{ adapter.quote(c.name) }}{{ ", " if not loop.last }}{%- endfor -%}
    {%- else -%}*{%- endif -%}
{%- endmacro %}


{#- Pre-hook: decide the days, compute their rows, drop corrections that change nothing, open the publications,
    delete the provisional rows of the days about to be written. -#}
{% macro mart_plan(calc, source_id) -%}
{%- set kind = var('mart_kind', none) -%}
{%- if execute and kind not in ('intraday', 'nightly') -%}
    {{ exceptions.raise_compiler_error("mart models need var mart_kind: intraday or nightly (got " ~ kind ~ ")") }}
{%- endif -%}
{%- set today = "(now() at time zone '" ~ var('report_timezone') ~ "')::date" -%}
{%- set table_name = this.schema ~ '.' ~ this.identifier -%}
{%- set cols = mart_columns(calc) -%}
create temp table mart_closed on commit drop as
select date_key, max(close_version) as close_version
  from meta.mart_day_publication
 where mart_table = '{{ table_name }}' and close_version >= 1
 group by date_key;

create temp table mart_days (date_key integer primary key, kind text not null, close_version integer not null,
                             publication_seq bigint) on commit drop;

-- Open days: from the day after the last closed one (or the first day with data) to today
with first_open as (
    select coalesce((select to_date(max(date_key)::text, 'YYYYMMDD') + 1 from mart_closed),
                    (select to_date(min(date_key)::text, 'YYYYMMDD') from {{ calc }})) as day
),
open_days as (
    select to_char(d, 'YYYYMMDD')::integer as date_key
      from first_open f, generate_series(f.day, {{ today }}, interval '1 day') d
     where f.day is not null
)
{%- if kind == 'intraday' %}
insert into mart_days (date_key, kind, close_version)
select date_key, 'intraday', 0
  from open_days
 where date_key >= to_char({{ today }} - 6, 'YYYYMMDD')::integer;
{%- else %},
final as ({{ mart_final_days(source_id, 'open_days') }}),
blocked as (
    select min(o.date_key) as date_key from open_days o
     where o.date_key not in (select * from final)
)
insert into mart_days (date_key, kind, close_version)
select o.date_key, 'nightly', 1
  from open_days o
 where o.date_key < coalesce((select date_key from blocked), 2147483647);

-- Corrections: the last 7 closed days, recomputed; kept below only where they differ
insert into mart_days (date_key, kind, close_version)
select date_key, 'correction', close_version + 1
  from mart_closed
 where date_key >= (select to_char(to_date(max(date_key)::text, 'YYYYMMDD') - 6, 'YYYYMMDD')::integer
                      from mart_closed);
{%- endif %}

create temp table mart_cand on commit drop as
select {{ cols }} from {{ calc }} where date_key in (select date_key from mart_days);

{% if kind == 'nightly' and is_incremental() -%}
-- A correction that changes no row is not a new version
delete from mart_days d
 where d.kind = 'correction'
   and not exists (
       (select {{ cols }} from mart_cand c where c.date_key = d.date_key
        except all
        select {{ cols }} from {{ this }} t where t.date_key = d.date_key and t.close_version = d.close_version - 1)
       union all
       (select {{ cols }} from {{ this }} t where t.date_key = d.date_key and t.close_version = d.close_version - 1
        except all
        select {{ cols }} from mart_cand c where c.date_key = d.date_key));
{%- endif %}

-- One publication per kind written; its number goes on every row of it
with p as (
    insert into meta.data_publication (kind, date_from, date_to, run_id)
    select kind, min(date_key), max(date_key), '{{ invocation_id }}'
      from mart_days group by kind
    returning publication_seq, kind
)
update mart_days d set publication_seq = p.publication_seq from p where p.kind = d.kind;

{% if is_incremental() -%}
-- The provisional rows of every day written are replaced (rows of closed versions are never touched)
delete from {{ this }} where close_version = 0 and date_key in (select date_key from mart_days);
{%- endif %}
{%- endmacro %}


{#- The model's select: the rows of the days to write, with their version and publication -#}
{% macro mart_rows(calc) -%}
select c.*, d.close_version, d.publication_seq
  from mart_cand c
  join mart_days d on d.date_key = c.date_key
{%- endmacro %}


{#- Post-hook: one record per day written, with its row count (zero included) -#}
{% macro mart_register() -%}
insert into meta.mart_day_publication (mart_table, date_key, close_version, publication_seq, row_count)
select '{{ this.schema }}.{{ this.identifier }}', d.date_key, d.close_version, d.publication_seq,
       (select count(*) from mart_cand c where c.date_key = d.date_key)
  from mart_days d
{%- endmacro %}
