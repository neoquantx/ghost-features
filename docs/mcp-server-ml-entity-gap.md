# Draft GitHub Issue

**Target repo:** `acryldata/mcp-server-datahub`  
**Title:** `get_entities` and `search` tools lack support for ML entity types (MLFeatureTable / MLFeature properties, MLModel search)

---

## Summary

Two concrete gaps were found in the MCP server's `get_entities` and `search` tools when working with ML entity types on GMS v1.5.0.6:

1. `get_entities` on `mlFeature` / `mlFeatureTable` URNs silently omits `mlFeatureProperties` (including `customProperties` and `sources`) from its GraphQL fragment, even though the aspect is present and fully populated on the entity.
2. Calling the `search` tool with `entity_type = "ML_MODEL"` raises a `ValidationError` because `ML_MODEL` is not included in the tool's entity-type enum.

---

## Environment

- DataHub GMS: `v1.5.0.6`
- MCP server: `acryldata/mcp-server-datahub` (latest `main` as of 2025-07)
- Transport: stdio MCP client

---

## Reproduction

### 1. Missing `mlFeatureProperties` in `get_entities`

Call `get_entities` with an `mlFeature` URN:

```python
result = await session.call_tool(
    "get_entities",
    {"urns": ["urn:li:mlFeature:(my_feature_table,my_feature)"]}
)
print(result)
```

**Observed response** — the returned entity object contains `urn`, `type`, and `ownership`, but `mlFeatureProperties` is absent:

```json
{
  "urn": "urn:li:mlFeature:(my_feature_table,my_feature)",
  "type": "ML_FEATURE",
  "ownership": null
}
```

**Expected** — `mlFeatureProperties` should be present, matching what the REST endpoint returns:

```bash
curl "http://localhost:8080/aspects/urn:li:mlFeature:(my_feature_table,my_feature)?aspect=mlFeatureProperties"
```

```json
{
  "mlFeatureProperties": {
    "customProperties": {"team": "ml-platform", "tier": "gold"},
    "sources": [
      {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,db.schema.events,PROD)"}
    ]
  }
}
```

The aspect exists and is retrievable via REST; the GraphQL fragment used by `get_entities` simply does not request it.

The same omission applies to `mlFeatureTable` URNs: `mlFeatureTableProperties` (which carries the list of `mlFeatures` and `mlPrimaryKeys`) is also absent from the fragment.

---

### 2. `ValidationError` when searching for `ML_MODEL` entities

```python
result = await session.call_tool(
    "search",
    {"entity_type": "ML_MODEL", "query": "*", "count": 10}
)
```

**Observed error:**

```
ValidationError: 1 validation error for SearchToolInput
entity_type
  value is not a valid enumeration member; permitted: 'DATASET', 'DASHBOARD',
  'CHART', 'DATA_FLOW', 'DATA_JOB', 'GLOSSARY_TERM', 'MLFEATURE_TABLE',
  'MLFEATURE', 'TAG', 'CORP_USER', 'CORP_GROUP', 'CONTAINER', 'DOMAIN'
  (type=type_error.enum; enum_values=[...])
```

`ML_MODEL`, `ML_MODEL_GROUP`, and `ML_PRIMARY_KEY` are not members of the enum, so there is no way to search or list model-catalog entities through the MCP tool layer.

---

## Root Cause (suspected)

- **`get_entities` fragment:** The GraphQL fragment for ML entity types in the MCP server does not include `mlFeatureProperties { customProperties sources { urn } }` or `mlFeatureTableProperties { mlFeatures { urn } mlPrimaryKeys { urn } }` sub-selections.
- **`search` enum:** The `EntityType` enum in the MCP server's Pydantic input model is missing `ML_MODEL`, `ML_MODEL_GROUP`, and `ML_PRIMARY_KEY` values.

---

## Impact

Any agent or integration that reads ML feature-store metadata or model catalogs via the MCP server receives incomplete data or hard errors, making the MCP layer unsuitable as a sole interface for ML observability use cases without falling back to direct REST calls.

This was discovered while building [ghost-features](https://github.com/himanshunikam/ghost-features), an ML observability agent that detects orphaned feature-store entries by tracing upstream warehouse lineage through DataHub's MCP server.

---

## Suggested Fix

1. **`get_entities` fragment** — add `mlFeatureProperties` and `mlFeatureTableProperties` sub-selections to the GraphQL fragment used for ML entity types, mirroring the fields already present for `datasetProperties`.
2. **`search` enum** — add `ML_MODEL`, `ML_MODEL_GROUP`, and `ML_PRIMARY_KEY` to the `EntityType` enum in the MCP server's input validation layer.

Happy to submit a PR for either or both if the approach looks right to maintainers.
