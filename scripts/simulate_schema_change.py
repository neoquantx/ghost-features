"""
Simulates a realistic upstream schema change: renames the 'cust_email' field
on the customers dataset to 'email_address', without updating the feature
store that depends on it. This is what causes a Ghost Feature.
"""

from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import SchemaMetadataClass

graph = DataHubGraph(DatahubClientConfig(server="http://localhost:8080"))

CUSTOMERS_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.customers,PROD)"
OLD_FIELD_NAME = "cust_email"
NEW_FIELD_NAME = "email_address"


def main():
    schema = graph.get_aspect(entity_urn=CUSTOMERS_URN, aspect_type=SchemaMetadataClass)
    if schema is None:
        raise RuntimeError(f"No schemaMetadata found for {CUSTOMERS_URN}")

    renamed = False
    for field in schema.fields:
        # fieldPath may be a simple name or a structured path; handle both
        if field.fieldPath == OLD_FIELD_NAME or field.fieldPath.endswith(f".{OLD_FIELD_NAME}"):
            print(f"Renaming field: {field.fieldPath} -> {NEW_FIELD_NAME}")
            field.fieldPath = field.fieldPath.replace(OLD_FIELD_NAME, NEW_FIELD_NAME)
            renamed = True

    if not renamed:
        print(f"Field '{OLD_FIELD_NAME}' not found on {CUSTOMERS_URN}. Nothing changed.")
        return

    graph.emit(MetadataChangeProposalWrapper(entityUrn=CUSTOMERS_URN, aspect=schema))
    print(f"\nSchema change applied to {CUSTOMERS_URN}.")
    print("Run detect_ghost_features.py again to see the ghost feature appear.")


if __name__ == "__main__":
    main()
