-- Contract if03d quality check (DD-94 item twelve, ICP question Q34): the newest version of a scope -- all the
-- datasets one version number covers -- has exactly the content of the version before it. Then the version number
-- does not mean what we assumed ("the configuration changed"), and every sync may be bumping it. A warning, not an
-- error: nothing is wrong with the data, but the assumption needs checking with ICP.
{{ config(severity='warn') }}
with snap as (
    select coalesce(split_part(snapshot_scope, '@', 2), '') as version_scope, snapshot_version,
           string_agg(encode(content_hash, 'hex'), ',' order by dataset, encode(content_hash, 'hex')) as content
      from {{ ref('stg_icp_reference') }}
     group by 1, 2
),
ranked as (
    select s.*, lag(content) over (partition by version_scope order by snapshot_version) as previous,
           row_number() over (partition by version_scope order by snapshot_version desc) as newest
      from snap s
)
select version_scope, snapshot_version from ranked where newest = 1 and content = previous
