"""
Data resources — expose static content or dynamic data via MCP resources.
"""


def register(mcp):

    @mcp.resource("nexa://info")
    def server_info() -> str:
        """Returns basic info about this NEXA MCP server."""
        return (
            "NEXA MCP Server\n"
            "Neural EXecution Agent — A real-time AI agent capable of executing\n"
            "actions through natural language using MCP.\n"
            "Built with FastMCP."
        )
