-- SCD2 (DD-12 · DD-94): the versions of one entity never overlap, each starts before it ends, and at most one is
-- current. A fact joins "the version valid at its time" (wait_begin >= valid_from and < valid_to); an overlap would
-- give a leg two agents, a gap no agent while the entity existed.
with v as (
    select 'dim_agent' as dim, src_natural_key, valid_from, valid_to from {{ ref('dim_agent') }}
    union all
    select 'dim_queue', src_natural_key, valid_from, valid_to from {{ ref('dim_queue') }}
)
select a.dim, a.src_natural_key, a.valid_from, a.valid_to, b.valid_from as other_from, b.valid_to as other_to
  from v a
  join v b on b.dim = a.dim and b.src_natural_key = a.src_natural_key and b.valid_from > a.valid_from
 where b.valid_from < a.valid_to
union all
select dim, src_natural_key, valid_from, valid_to, null, null from v where valid_from >= valid_to
