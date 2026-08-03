"""
Simulates an upstream type change: alters the nativeDataType of the
'unit_price' field on the order_items dataset from FLOAT to VARCHAR(16777216),
without updating the feature store. This causes avg_order_value to appear as
a TYPE_CHANGED ghost feature.
"""

from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import SchemaMetadataClass

graph = DataHubGraph(DatahubClientConfig(server="http://localhost:8080"))

ORDER_ITEMS_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.order_items,PROD)"
TARGET_FIELD = "unit_price"
NEW_TYPE = "VARCHAR(16777216)"


def main():
    schema = graph.get_aspect(entity_urn=ORDER_ITEMS_URN, aspect_type=SchemaMetadataClass)
    if schema is None:
        raise RuntimeError(f"No schemaMetadata found for {ORDER_ITEMS_URN}")

    changed = False
    for field in schema.fields:
        if field.fieldPath == TARGET_FIELD or field.fieldPath.endswith(f".{TARGET_FIELD}"):
            old_type = field.nativeDataType
            print(f"Field:    {field.fieldPath}")
            print(f"Old type: {old_type}")
            print(f"New type: {NEW_TYPE}")
            field.nativeDataType = NEW_TYPE
            changed = True

    if not changed:
        print(f"Field '{TARGET_FIELD}' not found on {ORDER_ITEMS_URN}. Nothing changed.")
        return

    graph.emit(MetadataChangeProposalWrapper(entityUrn=ORDER_ITEMS_URN, aspect=schema))
    print(f"\nType change applied to {ORDER_ITEMS_URN}.")
    print("Run detect_ghost_features.py to see the TYPE_CHANGED finding.")


if __name__ == "__main__":
    main()
