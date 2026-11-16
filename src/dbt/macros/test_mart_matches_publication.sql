{#- Generic test for every mart model (models/mart/schema.yml): the table holds exactly what meta.mart_day_publication
    says was published (DD-97 review · DD-99: the service layer picks each day's version from the record before it
    reads the rows, so the two must agree).
      - every group of rows (day, close_version, publication_seq) in the table is a recorded publication, with the
        recorded row count;
      - every recorded closed version, and the latest provisional write of a day not closed, has its rows (a
        zero-row record has none, and matches);
      - a closed day has no provisional rows left.
    One row per disagreement. -#}
{% test mart_matches_publication(model) %}
with rec as (
    select date_key, close_version, publication_seq, row_count
      from meta.mart_day_publication
     where mart_table = '{{ model.schema }}.{{ model.identifier }}'
),
current_rec as (
    select r.* from rec r
     where r.close_version >= 1
        or (r.publication_seq = (select max(x.publication_seq) from rec x
                                  where x.date_key = r.date_key and x.close_version = 0)
            and not exists (select 1 from rec x where x.date_key = r.date_key and x.close_version >= 1))
),
tbl as (
    select date_key, close_version, publication_seq, count(*) as row_count
      from {{ model }}
     group by date_key, close_version, publication_seq
)
select coalesce(t.date_key, c.date_key) as date_key, coalesce(t.close_version, c.close_version) as close_version,
       coalesce(t.publication_seq, c.publication_seq) as publication_seq,
       t.row_count as rows_in_table, c.row_count as rows_recorded,
       case when c.date_key is null then 'rows not recorded as the current publication of their day'
            else 'row count differs from the record' end as problem
  from tbl t
  full join current_rec c using (date_key, close_version, publication_seq)
 where coalesce(t.row_count, 0) <> coalesce(c.row_count, -1)
union all
select t.date_key, 0, t.publication_seq, t.row_count, null, 'provisional rows left on a closed day'
  from tbl t
 where t.close_version = 0 and exists (select 1 from rec r where r.date_key = t.date_key and r.close_version >= 1)
{% endtest %}
