"""FastAPI app builder for the ``qqchat_http`` channel.

This creates a completely independent HTTP service with its own:
- Tool whitelist  (only web-search + QQ chat retrieval)
- Skill scope     (only qqchat-search-* skills)
- Session store   (keyed by ``qqchat_http:{user_uin}:{session_id}``)
- Memory store    (per-account files)

None of these restrictions affect any other nanobot channel.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from loguru import logger

from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import QQChatCompatConfig
from nanobot.qqchat_compat.memory_store import AccountMemoryStore
from nanobot.qqchat_compat.planner import SkillDrivenPlanner
from nanobot.qqchat_compat.prompt_store import UserPromptStore
from nanobot.qqchat_compat.routes import create_router
from nanobot.qqchat_compat.session_store import SessionStore
from nanobot.qqchat_compat.tool_policy import ToolPolicy
from nanobot.qqchat_compat.user_config_store import UserConfigStore

if TYPE_CHECKING:
    from nanobot.config.schema import ToolsConfig


DEFAULT_ALLOWED_TOOLS = {
    "web_search",
    "web_fetch",
    "search_chats",
    "search_messages",
    "get_recent_messages",
    "get_recent_chats",
    "get_profiles",
}


def create_app(
    config: QQChatCompatConfig,
    workspace: Path,
    tools_config: ToolsConfig | None = None,
) -> FastAPI:
    """Create FastAPI app for QQChat compatibility layer.
    
    Args:
        config: QQChat compat configuration
        workspace: Workspace directory path
        tools_config: Optional tools configuration for MCP servers
    """
    app = FastAPI(title="QQ Chat Assistant API")

    # Setup tool registry and MCP connections
    tool_registry = ToolRegistry()
    mcp_stack = None
    
    @app.on_event("startup")
    async def startup_mcp():
        """Connect MCP servers on startup (but don't enable tools yet)."""
        nonlocal mcp_stack
        if tools_config and tools_config.mcp_servers:
            from nanobot.agent.tools.mcp import connect_mcp_servers
            
            try:
                mcp_stack = AsyncExitStack()
                await mcp_stack.__aenter__()
                await connect_mcp_servers(
                    tools_config.mcp_servers, tool_registry, mcp_stack, enabled=False
                )
                logger.info("QQChat compat: MCP servers connected, tools registered but disabled")
            except Exception as e:
                logger.error("QQChat compat: Failed to connect MCP servers: {}", e)
                if mcp_stack:
                    try:
                        await mcp_stack.aclose()
                    except Exception:
                        pass
                    mcp_stack = None

    @app.on_event("shutdown")
    async def shutdown_mcp():
        """Close MCP connections on shutdown."""
        if mcp_stack:
            try:
                await mcp_stack.aclose()
                logger.info("QQChat compat: MCP connections closed")
            except Exception as e:
                logger.warning("QQChat compat: Error closing MCP connections: {}", e)

    allowed_tools = set(config.allowed_tools) if config.allowed_tools else DEFAULT_ALLOWED_TOOLS
    policy = ToolPolicy(allowed_tools=allowed_tools)
    session_store = SessionStore(
        ttl_seconds=config.session_ttl_seconds,
        max_sessions=config.max_sessions,
    )
    memory_store = AccountMemoryStore(workspace)
    
    # Setup user prompt store with templates
    package_templates = Path(__file__).resolve().parent.parent / "templates"
    prompt_store = UserPromptStore(workspace, package_templates)
    
    # Setup user config store
    user_config_store = UserConfigStore(workspace / "users")
    
    package_skills_root = Path(__file__).resolve().parent.parent / "skills"
    workspace_skills_root = workspace / "skills"
    planner = SkillDrivenPlanner(
        skill_roots=[workspace_skills_root, package_skills_root],
        tool_registry=tool_registry,
        prompt_store=prompt_store,
    )

    app.include_router(
        create_router(
            session_store=session_store,
            memory_store=memory_store,
            prompt_store=prompt_store,
            user_config_store=user_config_store,
            planner=planner,
            policy=policy,
        )
    )
    return app
