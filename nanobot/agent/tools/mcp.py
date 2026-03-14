"""MCP client: connects to MCP servers and wraps their tools as native nanobot tools."""

import asyncio
from contextlib import AsyncExitStack
from typing import Any

import httpx
from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry


class MCPToolWrapper(Tool):
    """Wraps a single MCP server tool as a nanobot Tool."""

    def __init__(self, session, server_name: str, tool_def, tool_timeout: int = 30):
        self._session = session
        self._original_name = tool_def.name
        self._name = f"mcp_{server_name}_{tool_def.name}"
        self._description = tool_def.description or tool_def.name
        self._parameters = tool_def.inputSchema or {"type": "object", "properties": {}}
        self._tool_timeout = tool_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types

        try:
            result = await asyncio.wait_for(
                self._session.call_tool(self._original_name, arguments=kwargs),
                timeout=self._tool_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("MCP tool '{}' timed out after {}s", self._name, self._tool_timeout)
            return f"(MCP tool call timed out after {self._tool_timeout}s)"
        except asyncio.CancelledError:
            # MCP SDK's anyio cancel scopes can leak CancelledError on timeout/failure.
            # Re-raise only if our task was externally cancelled (e.g. /stop).
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            logger.warning("MCP tool '{}' was cancelled by server/SDK", self._name)
            return "(MCP tool call was cancelled)"
        except Exception as exc:
            logger.exception(
                "MCP tool '{}' failed: {}: {}",
                self._name,
                type(exc).__name__,
                exc,
            )
            return f"(MCP tool call failed: {type(exc).__name__})"

        parts = []
        for block in result.content:
            if isinstance(block, types.TextContent):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts) or "(no output)"


async def connect_single_mcp_server(
    server_name: str, cfg, registry: ToolRegistry, stack: AsyncExitStack, enabled: bool = False
) -> int:
    """Connect to a single MCP server and register its tools.
    
    Args:
        server_name: Name of the MCP server
        cfg: Server configuration object
        registry: Tool registry to register tools into
        stack: AsyncExitStack for resource management
        enabled: Whether to enable tools immediately (default: False, register but disable)
        
    Returns:
        Number of tools registered
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    try:
        transport_type = cfg.type
        if not transport_type:
            if cfg.command:
                transport_type = "stdio"
            elif cfg.url:
                # Convention: URLs ending with /sse use SSE transport; others use streamableHttp
                transport_type = (
                    "sse" if cfg.url.rstrip("/").endswith("/sse") else "streamableHttp"
                )
            else:
                logger.warning("MCP server '{}': no command or url configured, skipping", server_name)
                return 0

        if transport_type == "stdio":
            params = StdioServerParameters(
                command=cfg.command, args=cfg.args, env=cfg.env or None
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        elif transport_type == "sse":
            def httpx_client_factory(
                headers: dict[str, str] | None = None,
                timeout: httpx.Timeout | None = None,
                auth: httpx.Auth | None = None,
            ) -> httpx.AsyncClient:
                merged_headers = {**(cfg.headers or {}), **(headers or {})}
                return httpx.AsyncClient(
                    headers=merged_headers or None,
                    follow_redirects=True,
                    timeout=timeout,
                    auth=auth,
                )

            read, write = await stack.enter_async_context(
                sse_client(cfg.url, httpx_client_factory=httpx_client_factory)
            )
        elif transport_type == "streamableHttp":
            # Always provide an explicit httpx client so MCP HTTP transport does not
            # inherit httpx's default 5s timeout and preempt the higher-level tool timeout.
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=cfg.headers or None,
                    follow_redirects=True,
                    timeout=None,
                )
            )
            read, write, _ = await stack.enter_async_context(
                streamable_http_client(cfg.url, http_client=http_client)
            )
        else:
            logger.warning("MCP server '{}': unknown transport type '{}'", server_name, transport_type)
            return 0

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        tools = await session.list_tools()
        for tool_def in tools.tools:
            wrapper = MCPToolWrapper(session, server_name, tool_def, tool_timeout=cfg.tool_timeout)
            registry.register(wrapper, enabled=enabled)
            logger.debug("MCP: registered tool '{}' from server '{}' (enabled={})", wrapper.name, server_name, enabled)

        logger.info("MCP server '{}': connected, {} tools registered", server_name, len(tools.tools))
        return len(tools.tools)
    except Exception as e:
        logger.error("MCP server '{}': failed to connect: {}", server_name, e)
        return 0


async def connect_mcp_servers(
    mcp_servers: dict, registry: ToolRegistry, stack: AsyncExitStack, enabled: bool = False
) -> None:
    """Connect to configured MCP servers and register their tools.
    
    Args:
        mcp_servers: Dict of server_name -> config
        registry: Tool registry to register tools into
        stack: AsyncExitStack for resource management
        enabled: Whether to enable tools immediately (default: False for lazy loading)
    """
    for name, cfg in mcp_servers.items():
        await connect_single_mcp_server(name, cfg, registry, stack, enabled=enabled)
