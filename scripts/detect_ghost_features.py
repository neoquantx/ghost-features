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
    # Get the MLFeatureProperties aspect which contains `sources` and `customProperties`
    aspect = graph.get_aspect(entity_urn=feature_urn, aspect_type=MLFeaturePropertiesClass)
    if aspect is None:
        return {"feature": feature_urn, "status": "NO_ASPECT", "detail": "Feature has no mlFeatureProperties aspect"}

    if not getattr(aspect, "sources", None):
        return {"feature": feature_urn, "status": "NO_SOURCE", "detail": "Feature has no declared source"}

    source_urn = aspect.sources[0]

    # Expect the exact source field to be recorded in customProperties.sourceField
    expected_field_val = None
    if getattr(aspect, "customProperties", None):
        expected_field_val = aspect.customProperties.get("sourceField")

    if not expected_field_val:
        return {"feature": feature_urn, "status": "UNKNOWN", "detail": "No sourceField defined in feature customProperties"}

    # `sourceField` may be a full schemaField URN like
    # urn:li:schemaField:(<dataset_urn>,field_path) or just a field name.
    if isinstance(expected_field_val, str) and "schemaField" in expected_field_val:
        # parse trailing field path after the last comma
        expected_column = expected_field_val.split(",")[-1].rstrip(")")
    else:
        expected_column = expected_field_val

    current_fields = get_current_schema_fields(source_urn)

    if expected_column in current_fields:
        return {"feature": feature_urn, "status": "OK", "detail": f"Column '{expected_column}' present in {source_urn}", "expected_column": expected_column}
    else:
        return {
            "feature": feature_urn,
            "status": "GHOSTED",
            "detail": f"Column '{expected_column}' NOT FOUND in current schema of {source_urn}",
            "source": source_urn,
            "expected_column": expected_column,
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

        # Discover all mlModel entities and check each for usage of ghosted features.
        def model_uses_feature(model_aspect, feature_urn):
            if model_aspect is None or not getattr(model_aspect, "mlFeatures", None):
                return False
            for mf in model_aspect.mlFeatures:
                if isinstance(mf, str) and mf == feature_urn:
                    return True
                if hasattr(mf, "featureUrn") and getattr(mf, "featureUrn") == feature_urn:
                    return True
                if hasattr(mf, "feature") and getattr(mf, "feature") == feature_urn:
                    return True
                if str(mf) == feature_urn:
                    return True
            return False

        # Use graph.get_urns_by_filter to discover mlModel URNs across the catalog.
        all_model_urns = list(graph.get_urns_by_filter(entity_types=["mlModel"]))

        for g in ghosted:
            feature_urn = g["feature"]
            impacted_models = []
            for model_urn in all_model_urns:
                model_aspect = graph.get_aspect(entity_urn=model_urn, aspect_type=MLModelPropertiesClass)
                if model_uses_feature(model_aspect, feature_urn):
                    impacted_models.append(model_urn)

            # Print impact lines for every affected model
            for murn in impacted_models:
                try:
                    mname = murn.split(",")[1]
                except Exception:
                    mname = murn
                print("")
                print("⚠️  IMPACT: This feature is used by production model")
                print(f"    '{mname}' ({murn})")
                print("    This model may be silently consuming stale/missing data.")

            if impacted_models:
                # Create a native DataHub Document describing the finding, listing all impacted models
                feature_name = feature_urn.split(",")[-1].rstrip(")")
                expected_column = g.get("expected_column", "<unknown>")
                doc_id = f"ghost-feature-{re.sub('[^0-9a-zA-Z_-]+', '-', feature_name)}-{int(time.time())}"
                title = f"Ghost Feature Detected: {feature_name}"

                model_list_text = "\n".join([f"- {u}" for u in impacted_models])
                text = (
                    f"Feature `{feature_urn}` is ghosted: expected column '{expected_column}' "
                    f"is missing from its source table.\n\nImpacted production models:\n{model_list_text}\n\n"
                    "These models may be silently consuming stale or missing data."
                )

                doc = dhsdk.Document.create_document(
                    id=doc_id,
                    title=title,
                    text=text,
                    related_assets=[feature_urn] + impacted_models,
                )

                # Emit MCPS for the document
                for mcp in doc.as_mcps():
                    graph.emit(mcp)

                print(f"    Document created: {doc.urn}")
    else:
        print("✅ No ghost features detected.")

if __name__ == "__main__":
    main()
