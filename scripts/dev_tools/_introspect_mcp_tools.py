import asyncio

from mcp import ClientSession, StdioServerParameters, stdio_client


COMMAND = "/Users/himanshunikam/.local/bin/uvx"
ARGS = ["mcp-server-datahub@latest"]
ENV = {
    "DATAHUB_GMS_URL": "http://localhost:8080",
    "TOOLS_IS_MUTATION_ENABLED": "true",
}


async def main():
    server = StdioServerParameters(command=COMMAND, args=ARGS, env=ENV)

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_response = await session.list_tools()
            tools = getattr(tools_response, "tools", tools_response)

            for tool in tools:
                print(tool.name)


if __name__ == "__main__":
    asyncio.run(main())