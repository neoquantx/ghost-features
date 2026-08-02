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
    MLModelPropertiesClass,
)
from datahub import sdk as dhsdk
import time
import re

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

        # Check impact on known production model(s)
        MODEL_URN = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,fraud_risk_model_v1,PROD)"
        model_aspect = graph.get_aspect(entity_urn=MODEL_URN, aspect_type=MLModelPropertiesClass)

        def model_uses_feature(model_aspect, feature_urn):
            if model_aspect is None or not getattr(model_aspect, "mlFeatures", None):
                return False
            for mf in model_aspect.mlFeatures:
                # mf may be a string URN or an object with a featureUrn/feature field
                if isinstance(mf, str) and mf == feature_urn:
                    return True
                if hasattr(mf, "featureUrn") and getattr(mf, "featureUrn") == feature_urn:
                    return True
                if hasattr(mf, "feature") and getattr(mf, "feature") == feature_urn:
                    return True
                # fallback string compare
                if str(mf) == feature_urn:
                    return True
            return False

        for g in ghosted:
            feature_urn = g["feature"]
            if model_uses_feature(model_aspect, feature_urn):
                # extract model name for display
                try:
                    model_name = MODEL_URN.split(",")[1]
                except Exception:
                    model_name = MODEL_URN
                print("")
                print("⚠️  IMPACT: This feature is used by production model")
                print(f"    '{model_name}' ({MODEL_URN})")
                print("    This model may be silently consuming stale/missing data.")

                # Create a native DataHub Document describing the finding
                feature_name = feature_urn.split(",")[-1].rstrip(")")
                doc_id = f"ghost-feature-{re.sub('[^0-9a-zA-Z_-]+', '-', feature_name)}-{int(time.time())}"
                title = f"Ghost Feature Detected: {feature_name}"
                text = (
                    f"Feature `{feature_urn}` is ghosted: expected column '{FEATURE_COLUMN_MAP.get(feature_name)}' "
                    f"is missing from its source table.\n\nThis affects production model `{model_name}` ({MODEL_URN}), "
                    "which may be silently consuming stale or missing data."
                )

                doc = dhsdk.Document.create_document(
                    id=doc_id,
                    title=title,
                    text=text,
                    related_assets=[feature_urn, MODEL_URN],
                )

                # Emit MCPS for the document
                for mcp in doc.as_mcps():
                    graph.emit(mcp)

                print(f"    Document created: {doc.urn}")
    else:
        print("✅ No ghost features detected.")

if __name__ == "__main__":
    main()
