import json
from contextlib import asynccontextmanager
from contextlib import AsyncExitStack
from typing import AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_SERVER = StdioServerParameters(
    command="/Users/himanshunikam/.local/bin/uvx",
    args=["mcp-server-datahub@latest"],
    env={
        "DATAHUB_GMS_URL": "http://localhost:8080",
        "TOOLS_IS_MUTATION_ENABLED": "true",
    },
)


def _text(result) -> str:
    for block in result.content:
        if hasattr(block, "text"):
            return block.text
    raise ValueError(f"No text content in tool result: {result}")


class DataHubMCPClient:
    """Async context manager holding a single MCP server process for its lifetime."""

    def __init__(self):
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None

    async def __aenter__(self) -> "DataHubMCPClient":
        self._stack = AsyncExitStack()
        r, w = await self._stack.enter_async_context(stdio_client(_SERVER))
        self._session = await self._stack.enter_async_context(ClientSession(r, w))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._stack.aclose()

    async def get_entities(self, urns: list[str]) -> list[dict]:
        """Returns a list of entity dicts (one per URN; may contain an 'error' key)."""
        result = await self._session.call_tool("get_entities", {"urns": urns})
        return json.loads(_text(result))

    async def list_schema_fields(
        self, urn: str, keywords: list[str] | None = None
    ) -> dict:
        """Returns a dict with keys: urn, fields, totalFields, returned, remainingCount, …"""
        args: dict = {"urn": urn}
        if keywords is not None:
            args["keywords"] = keywords
        result = await self._session.call_tool("list_schema_fields", args)
        return json.loads(_text(result))

    async def save_document(
        self,
        title: str,
        content: str,
        document_type: str,
        related_assets: list[str] | None = None,
    ) -> str:
        """Creates a document and returns its URN."""
        args: dict = {"document_type": document_type, "title": title, "content": content}
        if related_assets is not None:
            args["related_assets"] = related_assets
        result = await self._session.call_tool("save_document", args)
        return json.loads(_text(result))["urn"]


@asynccontextmanager
async def open_client() -> AsyncIterator[DataHubMCPClient]:
    """Convenience: `async with open_client() as client:`"""
    async with DataHubMCPClient() as client:
        yield client
