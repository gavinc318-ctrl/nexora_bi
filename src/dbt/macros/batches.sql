{#- Consumption of raw by batch (DD-89 with its corrections).

    A batch is processable when it is not quarantined, or quarantined and released (DD-76): pending and discarded
    batches are never read. A model processes the processable batches it has not registered yet, and registers them
    in meta.staging_batch_state in the same transaction as its own write (a post-hook runs inside the model's
    transaction). That registry, not a time watermark, decides "processed or not": a batch ingested on Monday and
    released on Thursday is picked up by the next run.
-#}

{#- Batches of a source that this model may process now. -#}
{% macro unprocessed_batches(source_id, model_name) -%}
    select b.run_id, b.batch_seq
      from meta.ingest_batch b
     where b.source_id = '{{ source_id }}'
       and not exists (select 1 from meta.quarantine_batch q
                        where q.run_id = b.run_id and q.batch_seq = b.batch_seq
                          and q.state in ('pending', 'discarded'))
       and not exists (select 1 from meta.staging_batch_state s
                        where s.model = '{{ model_name }}'
                          and s.run_id = b.run_id and s.batch_seq = b.batch_seq)
{%- endmacro %}

{#- Post-hook of a staging model: register the batches whose rows this run wrote into the model's table, and the
    processable batches that had no rows at all. Both are exact: rows of a batch are only in the table if this or an
    earlier run wrote them (earlier runs registered theirs), and an empty batch has nothing to miss. A batch that
    arrived after the model selected its rows is NOT registered here -- it has no rows in the table yet. -#}
{% macro register_staged_batches(source_id) -%}
    insert into meta.staging_batch_state (model, run_id, batch_seq)
    select '{{ this.identifier }}', x.run_id, x.batch_seq
      from (select distinct run_id, batch_seq from {{ this }}
            union
            select b.run_id, b.batch_seq from meta.ingest_batch b
             where b.source_id = '{{ source_id }}' and b.row_count = 0) x
     where not exists (select 1 from meta.staging_batch_state s
                        where s.model = '{{ this.identifier }}' and s.run_id = x.run_id and s.batch_seq = x.batch_seq)
       and not exists (select 1 from meta.quarantine_batch q
                        where q.run_id = x.run_id and q.batch_seq = x.batch_seq and q.state in ('pending', 'discarded'))
{%- endmacro %}

{#- Rebuilding a staging model from raw (its table dropped and created again) must clear its registrations in the
    same transaction, or the rebuilt model would skip every batch it had processed before (change 0005). -#}
{% macro clear_registrations_on_rebuild() -%}
    {% if not is_incremental() %}
    delete from meta.staging_batch_state where model = '{{ this.identifier }}'
    {% else %}
    select 1
    {% endif %}
{%- endmacro %}
