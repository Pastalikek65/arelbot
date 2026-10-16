import logging
import json
import asyncio
from typing import Dict, List, Any, Optional
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # MCP is optional unless MCP_SERVERS is configured.
    ClientSession = Any  # type: ignore[misc,assignment]
    StdioServerParameters = None
    stdio_client = None
import os
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)

class McpManager:
    def __init__(self, servers_json: str):
        """
        Initializes the MCP Manager with a JSON string of server configurations.
        Example JSON:
        {
          "weather": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-weather"], "env": {"TAVILY_API_KEY": "..."}},
          "spotify": {"command": "node", "args": ["spotify-mcp.js"]}
        }
        """
        try:
            self.server_configs = json.loads(servers_json)
        except Exception as e:
            logger.error("Failed to parse MCP_SERVERS JSON: %s", summarize_exception(e, label="mcp_config"))
            self.server_configs = {}
            
        self.sessions: Dict[str, ClientSession] = {}
        self.exit_stacks = [] # To manage lifetimes of stdio clients
        self.tools_cache: List[Dict[str, Any]] = []

    async def connect_all(self):
        """Connects to all configured MCP servers and caches their tools."""
        if stdio_client is None:
            if self.server_configs:
                logger.warning("MCP is not installed; configured MCP servers are disabled")
            return
        for name, config in self.server_configs.items():
            try:
                command = config.get("command")
                args = config.get("args", [])
                env = config.get("env", {})
                
                params = StdioServerParameters(
                    command=command,
                    args=args,
                    env={**os.environ, **env} if env else None
                )
                
                # Note: This is a simplified async context manager usage for global lifespan
                from contextlib import AsyncExitStack
                stack = AsyncExitStack()
                self.exit_stacks.append(stack)
                
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                
                await session.initialize()
                self.sessions[name] = session
                
                # Fetch tools
                tools_res = await session.list_tools()
                for tool in tools_res.tools:
                    # Enrich tool name with server prefix to avoid collisions
                    tool_def = tool.model_dump()
                    tool_def["name"] = f"mcp_{name}_{tool_def['name']}"
                    self.tools_cache.append(tool_def)
                
                logger.info(f"âœ… Connected to MCP Server: {name}")
                
            except Exception as e:
                logger.error("Failed to connect to MCP server name=%s: %s", name, summarize_exception(e, label="mcp_connect"))

    def get_gemini_tools_def(self) -> List[Dict[str, Any]]:
        """Returns tools in a formatGemini expects."""
        gemini_tools = []
        for tool in self.tools_cache:
            gemini_tools.append({
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["inputSchema"]
            })
        return gemini_tools

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Executes a tool by its prefixed name."""
        if not tool_name.startswith("mcp_"):
            return None
            
        # Parse server and real tool name
        # Format: mcp_{server}_{real_name}
        parts = tool_name.split("_")
        if len(parts) < 3:
            return {"error": "Invalid MCP tool name format."}
            
        server_name = parts[1]
        real_tool_name = "_".join(parts[2:])
        
        session = self.sessions.get(server_name)
        if not session:
            return {"error": f"MCP session for '{server_name}' not active."}
            
        try:
            result = await session.call_tool(real_tool_name, arguments=args)
            return result.content
        except Exception as e:
            logger.error("MCP tool execution failed name=%s: %s", tool_name, summarize_exception(e, label="mcp_tool"))
            return {"error": "mcp_tool_execution_failed"}

    async def disconnect_all(self):
        """Cleans up all connections."""
        for stack in self.exit_stacks:
            await stack.aclose()
        self.sessions = {}
        self.exit_stacks = []
        self.tools_cache = []


