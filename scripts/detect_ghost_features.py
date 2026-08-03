"""
Ghost Feature Detector — scans ML feature-store metadata in DataHub and
flags any feature whose declared source column no longer exists or has
changed type in the current warehouse schema.

All DataHub reads/writes go through datahub_mcp_client (MCP) where the
MCP server exposes the capability.  Two gaps exist in this GMS v1.5.0.6
instance where the MCP server's GraphQL layer cannot reach ML entity types:

  1. mlFeatureProperties aspect (customProperties + sources) — fetched via
     the GMS REST /aspects endpoint.
  2. mlModel catalog scan — fetched via the GMS OpenAPI v3 endpoint, since
     the MCP search tool's GraphQL enum does not include ML_MODEL on this
     GMS version.

Every other interaction (feature-table entity, schema fields, document
creation) goes through open_client() / DataHubMCPClient.
"""

import asyncio
import re
import sys
import time

import aiohttp

sys.path.insert(0, "scripts")
from datahub_mcp_client import open_client

GMS_URL = "http://localhost:8080"
FEATURE_TABLE_URN = (
    "urn:li:mlFeatureTable:(urn:li:dataPlatform:feast,customer_risk_features)"
)


# ---------------------------------------------------------------------------
# GMS REST helpers (used only for the two MCP gaps noted above)
# ---------------------------------------------------------------------------

async def _gms_get(session: aiohttp.ClientSession, path: str) -> dict:
    async with session.get(f"{GMS_URL}{path}") as resp:
        resp.raise_for_status()
        return await resp.json()


async def fetch_feature_properties(
    http: aiohttp.ClientSession, feature_urn: str
) -> dict:
    """
    Returns the mlFeatureProperties value dict, e.g.:
      {"customProperties": {"sourceField": "...", "sourceFieldType": "..."},
       "sources": ["urn:li:dataset:..."], ...}
    Returns {} if the aspect is absent.
    """
    from urllib.parse import quote
    path = f"/aspects/{quote(feature_urn, safe='')}?aspect=mlFeatureProperties&version=0"
    try:
        data = await _gms_get(http, path)
        return data["aspect"]["com.linkedin.ml.metadata.MLFeatureProperties"]
    except Exception:
        return {}


async def fetch_all_models(http: aiohttp.ClientSession) -> list[dict]:
    """
    Returns list of dicts: [{"urn": ..., "mlFeatures": [...]}, ...]
    Uses the OpenAPI v3 entity endpoint — the only way to enumerate mlModel
    entities on this GMS version (MCP search tool's GraphQL enum excludes
    ML_MODEL).
    """
    try:
        data = await _gms_get(http, "/openapi/v3/entity/mlmodel?systemMetadata=false&count=100")
        result = []
        for e in data.get("entities", []):
            props = e.get("mlModelProperties", {}).get("value", {})
            result.append({
                "urn": e["urn"],
                "mlFeatures": props.get("mlFeatures", []),
            })
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------

async def check_feature(
    mcp_client,
    http: aiohttp.ClientSession,
    feature_urn: str,
) -> dict:
    props = await fetch_feature_properties(http, feature_urn)

    if not props:
        return {
            "feature": feature_urn,
            "status": "NO_ASPECT",
            "detail": "Feature has no mlFeatureProperties aspect",
        }

    sources = props.get("sources") or []
    if not sources:
        return {
            "feature": feature_urn,
            "status": "NO_SOURCE",
            "detail": "Feature has no declared source",
        }

    source_urn = sources[0]
    custom = props.get("customProperties") or {}
    source_field_val = custom.get("sourceField")

    if not source_field_val:
        return {
            "feature": feature_urn,
            "status": "UNKNOWN",
            "detail": "No sourceField defined in feature customProperties",
        }

    # sourceField may be a schemaField URN like
    # urn:li:schemaField:(<dataset_urn>,field_path) — take the last segment.
    if "schemaField" in source_field_val:
        expected_column = source_field_val.split(",")[-1].rstrip(")")
    else:
        expected_column = source_field_val

    recorded_type = custom.get("sourceFieldType")

    # --- MCP call: list_schema_fields ---
    schema = await mcp_client.list_schema_fields(source_urn)
    fields = schema.get("fields", [])
    current_field = next(
        (f for f in fields if f["fieldPath"].split(".")[-1] == expected_column),
        None,
    )

    if current_field is None:
        return {
            "feature": feature_urn,
            "status": "GHOSTED",
            "detail": (
                f"Column '{expected_column}' NOT FOUND in current schema of {source_urn}"
            ),
            "source": source_urn,
            "expected_column": expected_column,
        }

    current_type = current_field.get("nativeDataType")

    if recorded_type and current_type and recorded_type != current_type:
        return {
            "feature": feature_urn,
            "status": "TYPE_CHANGED",
            "detail": (
                f"Column '{expected_column}' changed type from "
                f"'{recorded_type}' to '{current_type}' in {source_urn}"
            ),
            "expected_column": expected_column,
            "recorded_type": recorded_type,
            "current_type": current_type,
        }

    return {
        "feature": feature_urn,
        "status": "OK",
        "detail": f"Column '{expected_column}' present in {source_urn}",
        "expected_column": expected_column,
        "current_type": current_type,
    }


async def main() -> None:
    print(f"Checking feature table: {FEATURE_TABLE_URN}\n")

    async with aiohttp.ClientSession() as http:
        async with open_client() as mcp:

            # Step 1 — get feature URNs via MCP
            ft_entities = await mcp.get_entities([FEATURE_TABLE_URN])
            ft = ft_entities[0]
            if "error" in ft:
                print(f"Feature table not found: {ft['error']}")
                return

            feature_urns = [
                f["urn"]
                for f in ft.get("featureTableProperties", {}).get("mlFeatures", [])
            ]

            if not feature_urns:
                print("No features found on this feature table. Aborting.")
                return

            # Step 2+3 — check each feature (MCP for schema, REST for aspect)
            results = []
            for urn in feature_urns:
                r = await check_feature(mcp, http, urn)
                results.append(r)

            print(f"{'STATUS':<14} FEATURE")
            print("-" * 70)
            for r in results:
                print(f"{r['status']:<14} {r['feature']}")
                print(f"               → {r['detail']}\n")

            ghosted_or_changed = [
                r for r in results if r["status"] in ("GHOSTED", "TYPE_CHANGED")
            ]

            if not ghosted_or_changed:
                print("✅ No ghost features detected.")
                return

            print(f"⚠️  {len(ghosted_or_changed)} problematic feature(s) detected!")

            # Step 4 — scan mlModel catalog (GMS REST, MCP can't search ML_MODEL)
            all_models = await fetch_all_models(http)

            for finding in ghosted_or_changed:
                feature_urn = finding["feature"]

                impacted_models = [
                    m["urn"]
                    for m in all_models
                    if feature_urn in m["mlFeatures"]
                ]

                for model_urn in impacted_models:
                    try:
                        model_name = model_urn.split(",")[1]
                    except Exception:
                        model_name = model_urn
                    print()
                    print("⚠️  IMPACT: This feature is used by production model")
                    print(f"    '{model_name}' ({model_urn})")
                    print("    This model may be silently consuming stale/missing data.")

                if impacted_models:
                    # Step 5 — save_document via MCP
                    feature_name = feature_urn.split(",")[-1].rstrip(")")
                    expected_column = finding.get("expected_column", "<unknown>")
                    title = f"Ghost Feature Detected: {feature_name}"
                    model_list_text = "\n".join(f"- {u}" for u in impacted_models)
                    content = (
                        f"Feature `{feature_urn}` is ghosted: expected column "
                        f"'{expected_column}' is missing from its source table.\n\n"
                        f"Impacted production models:\n{model_list_text}\n\n"
                        "These models may be silently consuming stale or missing data."
                    )
                    doc_urn = await mcp.save_document(
                        title=title,
                        content=content,
                        document_type="Analysis",
                        related_assets=[feature_urn] + impacted_models,
                    )
                    print(f"    Document created: {doc_urn}")


if __name__ == "__main__":
    asyncio.run(main())
