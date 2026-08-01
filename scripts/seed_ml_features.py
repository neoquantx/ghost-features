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
from datahub.metadata.schema_classes import (
    MLFeatureTablePropertiesClass,
    MLFeaturePropertiesClass,
    MLModelPropertiesClass,
    MLFeatureDataTypeClass,
    StatusClass,
)

emitter = DatahubRestEmitter(gms_server="http://localhost:8080")


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

sources = {
    "avg_order_value": order_items_urn,
    "customer_lifetime_orders": customers_urn,
    "customer_email_domain": customers_urn,
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
    emit_with_status(
        urn,
        MLFeaturePropertiesClass(
            description=f"{name} feature",
            dataType=(
                MLFeatureDataTypeClass.CONTINUOUS if is_numeric
                else MLFeatureDataTypeClass.TEXT
            ),
            sources=[sources[name]],
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