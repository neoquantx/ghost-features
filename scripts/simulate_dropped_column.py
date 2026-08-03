"""
Simulates an upstream dropped column: removes 'customer_id' entirely from
the customers dataset schema, without updating the feature store. This causes
customer_lifetime_orders to appear as a GHOSTED ghost feature.
"""

from datahub.ingestion.graph.client import DataHubGraph, DatahubClientConfig
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import SchemaMetadataClass

graph = DataHubGraph(DatahubClientConfig(server="http://localhost:8080"))

CUSTOMERS_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.customers,PROD)"
DROP_FIELD = "customer_id"


def main():
    schema = graph.get_aspect(entity_urn=CUSTOMERS_URN, aspect_type=SchemaMetadataClass)
    if schema is None:
        raise RuntimeError(f"No schemaMetadata found for {CUSTOMERS_URN}")

    before = len(schema.fields)
    schema.fields = [
        f for f in schema.fields
        if f.fieldPath != DROP_FIELD and not f.fieldPath.endswith(f".{DROP_FIELD}")
    ]
    after = len(schema.fields)

    if before == after:
        print(f"Field '{DROP_FIELD}' not found on {CUSTOMERS_URN}. Nothing changed.")
        return

    print(f"Dropped field: {DROP_FIELD}")
    print(f"Fields before: {before}  →  after: {after}")

    graph.emit(MetadataChangeProposalWrapper(entityUrn=CUSTOMERS_URN, aspect=schema))
    print(f"\nSchema change applied to {CUSTOMERS_URN}.")
    print("Run detect_ghost_features.py to see the GHOSTED finding for customer_lifetime_orders.")


if __name__ == "__main__":
    main()
