"""
NEXA MCP Server — Entry Point
Neural EXecution Agent | FastMCP server over SSE on port 8000

Run with: uv run nexa
"""

from mcp.server.fastmcp import FastMCP

from nexa.config import config
from nexa.execution.context import context
from nexa.execution.logger import trace
from nexa.prompts import register_all_prompts
from nexa.resources import register_all_resources
from nexa.tools import register_all_tools

# ---------------------------------------------------------------------------
# MCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name=config.SERVER_NAME,
    instructions=(
        "You are NEXA, a Neural EXecution Agent. "
        "You have access to tools to execute tasks through natural language. "
        "Be concise, accurate, and direct."
    ),
)

# ---------------------------------------------------------------------------
# Register domain tools, prompts, and resources
# ---------------------------------------------------------------------------

register_all_tools(mcp)
register_all_prompts(mcp)
register_all_resources(mcp)

# ---------------------------------------------------------------------------
# Observability tools — only exposed when DEBUG=true
# ---------------------------------------------------------------------------

if config.DEBUG:

    @mcp.tool()
    def get_debug_trace() -> str:
        """
        Return the structured execution trace for the current/last turn.
        Only available when DEBUG=true. Use to inspect agent decision flow.
        """
        return trace.get_debug_trace()

    @mcp.tool()
    def get_session_context() -> dict:
        """
        Return the current session context (intent history, tool stats, turn count).
        Only available when DEBUG=true.
        """
        return context.to_dict()

    @mcp.tool()
    def reset_trace() -> str:
        """
        Clear the current trace buffer. Useful for isolating a specific turn.
        Only available when DEBUG=true.
        """
        trace.reset()
        return "Trace buffer cleared."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
