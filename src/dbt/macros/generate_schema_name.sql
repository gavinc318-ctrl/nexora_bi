{#- A model's schema is exactly the one configured (staging, core, mart, ai), not dbt's default "<target>_<custom>":
    the schemas exist, belong to fixed roles and carry the grants of change 0002. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name is not none else target.schema }}
{%- endmacro %}
