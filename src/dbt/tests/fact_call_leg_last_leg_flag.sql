-- is_last_leg is exactly leg_seq = -1, and release_source is set on the final leg only (DD-91)
select call_leg_sk
  from {{ ref('fact_call_leg') }}
 where is_last_leg <> (leg_seq = -1)
    or (not is_last_leg and release_source is not null)
    or (is_last_leg and release_source is null)
