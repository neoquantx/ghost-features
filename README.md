# Ghost Features

Ghost Features is an ML observability agent that detects orphaned feature-store entries — ML features whose upstream warehouse columns have been renamed, dropped, or changed type without the feature store being updated. It works by cross-referencing DataHub's live warehouse schema metadata against the declared source columns in each feature's `mlFeatureProperties`, using DataHub's MCP Server as the primary interface for reads and writes.

---

## The Problem

ML models that depend on a feature store have a silent failure mode that standard health checks miss entirely. The model runs, the pipeline completes, no exceptions are raised — but the feature values being consumed are stale, wrong, or absent, because the upstream warehouse column the feature was computed from has quietly changed.

This happens because feature-store metadata (which column a feature maps to, what type that column had at registration time) is written once at feature creation and rarely audited again. Meanwhile, warehouse schemas evolve continuously: columns get renamed during refactors, dropped when tables are restructured, or silently change type when upstream ETL logic changes. There is no automatic feedback loop between the warehouse and the feature store. The result is a "ghost feature" — an entry that looks valid in the catalog but is disconnected from reality in production.

---

## Architecture

```
┌─────────────────────────────────────┐
│             DataHub GMS             │
│  • schemaMetadata  (warehouse cols) │
│  • mlFeatureProperties (sources)    │
│  • mlModel catalog                  │
│  • Documents (remediation notes)    │
└────────────────┬────────────────────┘
                 │  MCP protocol (stdio)
                 ▼
        ┌────────────────┐
        │   MCP Server   │  acryldata/mcp-server-datahub
        └───────┬────────┘
                │  get_entities / list_schema_fields / save_document
                ▼
   ┌─────────────────────────┐
   │  detect_ghost_features  │──► writes remediation Documents back to DataHub
   └─────────────────────────┘
```

The detector's reads and writes go through the MCP Server. There are two documented exceptions where GMS v1.5.0.6's MCP tools do not yet expose ML entity metadata:

1. `mlFeatureProperties` (the `customProperties` and `sources` fields on each feature) — fetched via the GMS REST `/aspects` endpoint directly.
2. `mlModel` catalog enumeration — fetched via the GMS OpenAPI v3 endpoint, because `ML_MODEL` is absent from the MCP search tool's GraphQL entity-type enum on this GMS version.

Both gaps are documented in [`docs/mcp-server-ml-entity-gap.md`](docs/mcp-server-ml-entity-gap.md), which is a drafted issue ready to file upstream against `acryldata/mcp-server-datahub`.

The seeding and simulation scripts (`scripts/seed_ml_features.py`, `scripts/simulate_*.py`) use the DataHub Python SDK directly. These are one-time environment setup tools, not part of the agent runtime, so direct SDK use is appropriate and consistent with how DataHub's own ingestion tooling works.

---

## Setup

### 1. Install Docker and start DataHub

```bash
# Install Docker Desktop from https://www.docker.com/products/docker-desktop
# Then install the DataHub CLI
pip install acryl-datahub

# Start a local DataHub instance (pulls ~4 GB of images on first run)
datahub docker quickstart
```

DataHub UI will be available at http://localhost:9002. GMS runs at http://localhost:8080.

### 2. Load the showcase ecommerce datapack

```bash
datahub docker ingest-sample-data
```

This seeds the Snowflake `order_entry` schema (customers, order_items, etc.) that the feature store references.

### 3. Clone this repo and install dependencies

```bash
git clone https://github.com/himanshunikam/ghost-features.git
cd ghost-features

# Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv pip install -r requirements.txt
```

### 4. Start the DataHub MCP Server

```bash
# In a separate terminal — keep this running while the detector runs
uvx mcp-server-datahub --transport stdio
```

The detector launches this automatically via `scripts/datahub_mcp_client.py`; you do not need to start it manually unless you want to inspect MCP traffic directly.

### 5. Seed ML feature-store metadata

```bash
python3 scripts/seed_ml_features.py
```

This creates the `customer_risk_features` feature table, three ML features (`avg_order_value`, `customer_lifetime_orders`, `customer_email_domain`), and the `fraud_risk_model_v1` ML model in DataHub, with lineage wired to the Snowflake source columns.

---

## Usage

### Run the detector

```bash
python3 scripts/detect_ghost_features.py
```

The detector scans every feature in the `customer_risk_features` feature table, compares each feature's declared source column and type against the live warehouse schema, and prints a status for each:

| Status | Meaning |
|---|---|
| `OK` | Source column exists and type matches what was recorded at feature registration. |
| `GHOSTED` | Source column no longer exists in the warehouse schema (renamed or dropped). |
| `TYPE_CHANGED` | Source column exists but its `nativeDataType` differs from the type recorded in `mlFeatureProperties.customProperties.sourceFieldType`. |

For any `GHOSTED` or `TYPE_CHANGED` finding, the detector also scans the ML model catalog for any production model that lists the affected feature, prints an impact warning, and writes a remediation Document back into DataHub via the MCP Server.

### Reproduce each ghosting scenario

**Scenario 1 — renamed column** (causes `customer_email_domain` → `GHOSTED`):
```bash
python3 scripts/simulate_schema_change.py
```
Renames `cust_email` to `email_address` on the customers dataset.

**Scenario 2 — type change** (causes `avg_order_value` → `TYPE_CHANGED`):
```bash
python3 scripts/simulate_type_change.py
```
Changes `unit_price` from `FLOAT` to `VARCHAR(16777216)` on the order_items dataset.

**Scenario 3 — dropped column** (causes `customer_lifetime_orders` → `GHOSTED`):
```bash
python3 scripts/simulate_dropped_column.py
```
Removes `customer_id` entirely from the customers dataset schema.

Run all three simulations and then the detector to see all findings at once:

```bash
python3 scripts/simulate_schema_change.py
python3 scripts/simulate_type_change.py
python3 scripts/simulate_dropped_column.py
python3 scripts/detect_ghost_features.py
```

---

## Example Output

The following is a real run after all three simulations have been applied ([full output](examples/sample_run_output.txt)):

```
Checking feature table: urn:li:mlFeatureTable:(urn:li:dataPlatform:feast,customer_risk_features)

STATUS         FEATURE
----------------------------------------------------------------------
TYPE_CHANGED   urn:li:mlFeature:(customer_risk_features,avg_order_value)
               → Column 'unit_price' changed type from 'FLOAT' to 'VARCHAR(16777216)' in urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.order_items,PROD)

GHOSTED        urn:li:mlFeature:(customer_risk_features,customer_lifetime_orders)
               → Column 'customer_id' NOT FOUND in current schema of urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.customers,PROD)

GHOSTED        urn:li:mlFeature:(customer_risk_features,customer_email_domain)
               → Column 'cust_email' NOT FOUND in current schema of urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.customers,PROD)

⚠️  3 problematic feature(s) detected!

⚠️  IMPACT: This feature is used by production model
    'fraud_risk_model_v1' (urn:li:mlModel:(urn:li:dataPlatform:mlflow,fraud_risk_model_v1,PROD))
    This model may be silently consuming stale/missing data.
    Document created: urn:li:document:shared-246c8e5e-cd3e-405b-af82-34fe3b0488da

⚠️  IMPACT: This feature is used by production model
    'fraud_risk_model_v1' (urn:li:mlModel:(urn:li:dataPlatform:mlflow,fraud_risk_model_v1,PROD))
    This model may be silently consuming stale/missing data.
    Document created: urn:li:document:shared-a1178024-7209-4d14-9f05-96fe4ef510a9

⚠️  IMPACT: This feature is used by production model
    'fraud_risk_model_v1' (urn:li:mlModel:(urn:li:dataPlatform:mlflow,fraud_risk_model_v1,PROD))
    This model may be silently consuming stale/missing data.
    Document created: urn:li:document:shared-601fbeed-62c2-4e89-9984-dcc137d8825c
```

Each `Document created` line is a live DataHub Document entity, linked to the affected feature and impacted model, visible in the DataHub UI. See [`examples/sample_remediation_document.md`](examples/sample_remediation_document.md) for the content of one such document.

---

## Open-Source Contributions

While building this agent, two gaps were found in the DataHub MCP server's support for ML entity types. A detailed bug report with minimal reproductions is drafted and ready to file upstream:

- [`docs/mcp-server-ml-entity-gap.md`](docs/mcp-server-ml-entity-gap.md) — covers missing `mlFeatureProperties` in `get_entities` GraphQL fragments and a `ValidationError` when searching for `ML_MODEL` entities via the MCP search tool on GMS v1.5.0.6.

---

## Future Work

- Attach DataHub owners and tags to generated Documents so findings are routed to the right team automatically.
- Slack and email alerting when new ghost features are detected, without requiring a manual detector run.
- Unit tests and CI so schema-change simulations and detection logic are regression-tested on each commit.
- Full catalog-wide ML entity scan (all feature tables, not just one) once the MCP gap for `mlFeatureProperties` and `ML_MODEL` search is fixed upstream.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
