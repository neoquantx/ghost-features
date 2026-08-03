import asyncio
import json
import sys

sys.path.insert(0, "scripts")
from datahub_mcp_client import open_client


async def main():
    async with open_client() as client:
        print("=== get_entities ===")
        entities = await client.get_entities(
            ["urn:li:mlFeatureTable:(urn:li:dataPlatform:feast,customer_risk_features)"]
        )
        print(json.dumps(entities, indent=2))

        print("\n=== list_schema_fields ===")
        fields = await client.list_schema_fields(
            "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.customers,PROD)"
        )
        print(json.dumps(fields, indent=2))

        print("\n=== save_document ===")
        urn = await client.save_document(
            title="MCP client test",
            content="Testing the MCP client wrapper end to end.",
            document_type="Note",
        )
        print(urn)


if __name__ == "__main__":
    asyncio.run(main())
