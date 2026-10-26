{#- Reading the contract in SQL. The contract's runtime copy is meta.source_contract (LD-01 2.1); value sets and
    classifications are registered there, not written into models (LD-01 4.3 · DD-91 item 5). -#}

{% macro contract_path(source_id, path) -%}
    (select c.definition #> '{{ "{" ~ path ~ "}" }}' from meta.source_contract c where c.source_id = '{{ source_id }}')
{%- endmacro %}

{% macro contract_text(source_id, path) -%}
    (select c.definition #>> '{{ "{" ~ path ~ "}" }}' from meta.source_contract c where c.source_id = '{{ source_id }}')
{%- endmacro %}
