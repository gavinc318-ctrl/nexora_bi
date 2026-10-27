{#- dbt-postgres' own get_columns_in_relation, with generated columns left out.

    The SCD2 dimensions have is_current GENERATED ALWAYS AS (valid_to = 'infinity') (ddl/02). An incremental model
    inserts into every column dbt finds in the target table, and PostgreSQL refuses any value for a generated
    column -- so without this the dimension models cannot be written at all. A generated column can never be
    written, so leaving it out of the list changes nothing else. Copied from dbt-postgres 1.x
    (include/postgres/macros/adapters.sql); the only change is the is_generated condition.
-#}
{% macro postgres__get_columns_in_relation(relation) -%}
  {% call statement('get_columns_in_relation', fetch_result=True) %}
      select
          column_name,
          data_type,
          character_maximum_length,
          numeric_precision,
          numeric_scale

      from {{ relation.information_schema('columns') }}
      where table_name = '{{ relation.identifier }}'
        {% if relation.schema %}
        and table_schema = '{{ relation.schema }}'
        {% endif %}
        and is_generated = 'NEVER'
      order by ordinal_position

  {% endcall %}
  {% set table = load_result('get_columns_in_relation').table %}
  {{ return(sql_convert_columns_in_relation(table)) }}
{% endmacro %}
