"""
Ghost Feature Detector — scans ML feature-store metadata in DataHub and
flags any feature whose declared source column no longer exists in the
current warehouse schema.
"""

from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.metadata.schema_classes import (
    MLFeatureTablePropertiesClass,
    MLFeaturePropertiesClass,
    SchemaMetadataClass,
)

graph = DataHubGraph(DatahubClientConfig(server="http://localhost:8080"))

FEATURE_TABLE_URN = "urn:li:mlFeatureTable:(urn:li:dataPlatform:feast,customer_risk_features)"

# Map of feature name -> the specific column name it depends on in its source.
# In a real system this would be parsed from feature definitions; here we
# encode it explicitly since we know what we seeded.
FEATURE_COLUMN_MAP = {
    "avg_order_value": "unit_price",
    "customer_lifetime_orders": "customer_id",
    "customer_email_domain": "cust_email",
}

def get_feature_table_features(feature_table_urn):
    aspect = graph.get_aspect(
        entity_urn=feature_table_urn,
        aspect_type=MLFeatureTablePropertiesClass,
    )
    if aspect is None:
        raise RuntimeError(f"Feature table not found: {feature_table_urn}")
    return aspect.mlFeatures or []

def get_feature_source(feature_urn):
    aspect = graph.get_aspect(
        entity_urn=feature_urn,
        aspect_type=MLFeaturePropertiesClass,
    )
    if aspect is None or not aspect.sources:
        return None
    return aspect.sources[0]  # our features have exactly one source each

def get_current_schema_fields(dataset_urn):
    aspect = graph.get_aspect(
        entity_urn=dataset_urn,
        aspect_type=SchemaMetadataClass,
    )
    if aspect is None:
        return set()
    # fieldPath often includes full nested path; take the last segment
    return {f.fieldPath.split(".")[-1] for f in aspect.fields}

def check_feature(feature_urn):
    source_urn = get_feature_source(feature_urn)
    if source_urn is None:
        return {"feature": feature_urn, "status": "NO_SOURCE", "detail": "Feature has no declared source"}

    feature_name = feature_urn.split(",")[-1].rstrip(")")
    expected_column = FEATURE_COLUMN_MAP.get(feature_name)

    current_fields = get_current_schema_fields(source_urn)

    if expected_column is None:
        return {"feature": feature_urn, "status": "UNKNOWN", "detail": "No column mapping defined for this feature"}

    if expected_column in current_fields:
        return {"feature": feature_urn, "status": "OK", "detail": f"Column '{expected_column}' present in {source_urn}"}
    else:
        return {
            "feature": feature_urn,
            "status": "GHOSTED",
            "detail": f"Column '{expected_column}' NOT FOUND in current schema of {source_urn}",
            "source": source_urn,
        }

def main():
    print(f"Checking feature table: {FEATURE_TABLE_URN}\n")
    feature_urns = get_feature_table_features(FEATURE_TABLE_URN)

    if not feature_urns:
        print("No features found on this feature table. Aborting.")
        return

    results = [check_feature(urn) for urn in feature_urns]

    print(f"{'STATUS':<10} {'FEATURE'}")
    print("-" * 60)
    for r in results:
        print(f"{r['status']:<10} {r['feature']}")
        print(f"           → {r['detail']}\n")

    ghosted = [r for r in results if r["status"] == "GHOSTED"]
    if ghosted:
        print(f"⚠️  {len(ghosted)} ghost feature(s) detected!")
    else:
        print("✅ No ghost features detected.")

if __name__ == "__main__":
    main()
