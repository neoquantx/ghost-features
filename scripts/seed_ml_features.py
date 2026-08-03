"""
Seeds synthetic ML feature-store metadata (MLFeatureTable, MLFeature, MLModel)
into a local DataHub instance, wired to real upstream tables from the
showcase-ecommerce datapack. This lets us simulate a "ghost feature" scenario
later by renaming/dropping a source column and detecting the mismatch.
"""

from datahub.emitter.mce_builder import (
    make_dataset_urn,
    make_ml_feature_urn,
    make_ml_feature_table_urn,
    make_ml_model_urn,
)
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.metadata.schema_classes import SchemaFieldClass, SchemaMetadataClass
from datahub.metadata.schema_classes import (
    MLFeatureTablePropertiesClass,
    MLFeaturePropertiesClass,
    MLModelPropertiesClass,
    MLFeatureDataTypeClass,
    StatusClass,
)

emitter = DatahubRestEmitter(gms_server="http://localhost:8080")
graph = DataHubGraph(DatahubClientConfig(server="http://localhost:8080"))


def emit_with_status(urn, aspect):
    emitter.emit(MetadataChangeProposalWrapper(entityUrn=urn, aspect=aspect))
    emitter.emit(MetadataChangeProposalWrapper(entityUrn=urn, aspect=StatusClass(removed=False)))

# NOTE: replace "b2fd91" below if your datapack load generated a different
# org prefix — check your ingestion log output or search the UI for
# "customers" to confirm the exact dataset URN.
PLATFORM_PREFIX = "b2fd91"

customers_urn = make_dataset_urn(
    platform="snowflake",
    name=f"{PLATFORM_PREFIX}.order_entry_db.order_entry.customers",
    env="PROD",
)
order_items_urn = make_dataset_urn(
    platform="snowflake",
    name=f"{PLATFORM_PREFIX}.order_entry_db.order_entry.order_items",
    env="PROD",
)

feature_table_urn = make_ml_feature_table_urn(
    platform="feast", feature_table_name="customer_risk_features"
)

feature_urns = {
    "avg_order_value": make_ml_feature_urn("customer_risk_features", "avg_order_value"),
    "customer_lifetime_orders": make_ml_feature_urn("customer_risk_features", "customer_lifetime_orders"),
    "customer_email_domain": make_ml_feature_urn("customer_risk_features", "customer_email_domain"),
}

# Point each feature source at the specific schemaField URN for the column it
# depends on (this lets lineage track exactly which column each feature uses).
# The DataHub GMS may reject schemaField URNs directly in the `sources` field
# for MLFeatureProperties; to remain compatible we keep `sources` as the dataset
# URN but record the exact schema field URN in `customProperties.sourceField`.
sources = {
    "avg_order_value": order_items_urn,
    "customer_lifetime_orders": customers_urn,
    "customer_email_domain": customers_urn,
}

# Map from feature -> the exact source field path (for downstream tracing)
source_fields = {
    "avg_order_value": "unit_price",
    "customer_lifetime_orders": "customer_id",
    "customer_email_domain": "cust_email",
}

# 1. Feature table
emit_with_status(
    feature_table_urn,
    MLFeatureTablePropertiesClass(
        description="Customer risk features used by fraud detection models",
        mlFeatures=list(feature_urns.values()),
    ),
)

# 2. Individual features pointing at real source columns
for name, urn in feature_urns.items():
    is_numeric = "value" in name or "orders" in name
    # Determine the current native data type for the source field
    try:
        schema_aspect = graph.get_aspect(entity_urn=sources[name], aspect_type=SchemaMetadataClass)
        field_type = None
        if schema_aspect and getattr(schema_aspect, 'fields', None):
            for f in schema_aspect.fields:
                # compare last segment
                if f.fieldPath.split('.')[-1] == source_fields[name]:
                    field_type = getattr(f, 'nativeDataType', None)
                    break
    except Exception:
        field_type = None

    custom_props = {
        "sourceField": f"urn:li:schemaField:({sources[name]},{source_fields[name]})"
    }
    if field_type:
        custom_props["sourceFieldType"] = field_type

    emit_with_status(
        urn,
        MLFeaturePropertiesClass(
            description=f"{name} feature",
            dataType=(
                MLFeatureDataTypeClass.CONTINUOUS if is_numeric
                else MLFeatureDataTypeClass.TEXT
            ),
            sources=[sources[name]],
            customProperties=custom_props,
        ),
    )

# 3. Model consuming the feature table
model_urn = make_ml_model_urn(platform="mlflow", model_name="fraud_risk_model_v1", env="PROD")
emit_with_status(
    model_urn,
    MLModelPropertiesClass(
        description="Fraud risk scoring model, deployed in production",
        mlFeatures=list(feature_urns.values()),
    ),
)

print("Seeded ML feature store metadata successfully.")