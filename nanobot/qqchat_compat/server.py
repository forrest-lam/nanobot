"""FastAPI app builder for the ``qqchat_http`` channel.

This creates a completely independent HTTP service with its own:
- Tool whitelist  (only web-search + QQ chat retrieval)
- Skill scope     (only qqchat-search-* skills)
- Session store   (keyed by ``qqchat_http:{user_uin}:{session_id}``)
- Memory store    (per-account files)

None of these restrictions affect any other nanobot channel.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import QQChatCompatConfig
from nanobot.qqchat_compat.memory_store import AccountMemoryStore
from nanobot.qqchat_compat.planner import SkillDrivenPlanner
from nanobot.qqchat_compat.prompt_store import UserPromptStore
from nanobot.qqchat_compat.routes import create_router
from nanobot.qqchat_compat.session_store import SessionStore
from nanobot.qqchat_compat.tool_policy import ToolPolicy
from nanobot.qqchat_compat.user_config_store import UserConfigStore
from nanobot.session.manager import SessionManager

if TYPE_CHECKING:
    from nanobot.config.schema import Config, ToolsConfig
    from nanobot.providers.base import LLMProvider


DEFAULT_ALLOWED_TOOLS = {
    # qq-search-MCP-Server 提供的工具
    "qqsearch",  # QQ域内搜索(空间/群/功能/频道/小程序/小游戏)
    "sougou_search",  # 搜狗搜索引擎(替代web_search/web_fetch)
    "qiwei_doc",  # 企业微信智能文档记录badcase
    # 注意: 聊天工具(search_chats等)由客户端在/init时传入,不在此白名单
}


def create_app(
    config: QQChatCompatConfig,
    workspace: Path,
    provider: LLMProvider,
    full_config: Config,
    tools_config: ToolsConfig | None = None,
) -> FastAPI:
    """Create FastAPI app for QQChat compatibility layer.
    
    Args:
        config: QQChat compat configuration
        workspace: Workspace directory path
        provider: LLM provider instance
        full_config: Full nanobot config
        tools_config: Optional tools configuration for MCP servers
    """
    
    # Prepare tool whitelist
    allowed_tools = set(config.allowed_tools) if config.allowed_tools else DEFAULT_ALLOWED_TOOLS
    
    # Create AgentLoop early so it can be used in lifespan
    bus = MessageBus()
    session_manager = SessionManager(workspace)
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        model=full_config.agents.defaults.model,
        max_iterations=full_config.agents.defaults.max_tool_iterations,
        context_window_tokens=full_config.agents.defaults.context_window_tokens,
        web_search_config=full_config.tools.web.search,
        web_proxy=full_config.tools.web.proxy,
        exec_config=full_config.tools.exec,
        session_manager=session_manager,
        mcp_servers=full_config.tools.mcp_servers,
        channels_config=full_config.channels,
        restrict_to_workspace=full_config.tools.restrict_to_workspace,
        allowed_tools=allowed_tools,  # Pass whitelist to restrict tools
        channel="qqchat_http",  # Set channel to load qqchat-specific skills
    )
    
    # Hold MCP stack reference for cleanup
    mcp_stack_holder = {"stack": None}
    
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage MCP connections lifecycle - connect BEFORE any requests."""
        # Startup: Connect MCP servers BEFORE planner can try to activate skills
        if tools_config and tools_config.mcp_servers:
            from nanobot.agent.tools.mcp import connect_mcp_servers
            
            try:
                mcp_stack = AsyncExitStack()
                await mcp_stack.__aenter__()
                mcp_stack_holder["stack"] = mcp_stack
                
                # Connect MCP servers to AgentLoop's tool registry
                # Don't add prefix in qqchat compat mode to match client tool names
                await connect_mcp_servers(
                    tools_config.mcp_servers, 
                    agent_loop.tools,  # Use AgentLoop's registry
                    mcp_stack, 
                    enabled=False,  # Register as disabled first
                    add_prefix=False
                )
                
                # Enable tools that are in the whitelist
                enabled_count = 0
                for tool_name in allowed_tools:
                    if agent_loop.tools.enable(tool_name) > 0:
                        enabled_count += 1
                
                logger.info(
                    "QQChat compat: MCP connected, {} tools total, {} enabled",
                    len(agent_loop.tools.tool_names), enabled_count
                )
            except Exception as e:
                logger.error("QQChat compat: Failed to connect MCP servers: {}", e)
                if mcp_stack_holder["stack"]:
                    try:
                        await mcp_stack_holder["stack"].aclose()
                    except Exception:
                        pass
                    mcp_stack_holder["stack"] = None
        
        yield  # Application runs
        
        # Shutdown: Close MCP connections
        if mcp_stack_holder["stack"]:
            try:
                await mcp_stack_holder["stack"].aclose()
                logger.info("QQChat compat: MCP connections closed")
            except Exception as e:
                logger.warning("QQChat compat: Error closing MCP connections: {}", e)
    
    app = FastAPI(title="QQ Chat Assistant API", lifespan=lifespan)

    # ToolPolicy is a dataclass, initialize with allowed_tools
    policy = ToolPolicy(allowed_tools=allowed_tools)  # type: ignore[call-arg]
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
    
    # Setup planner with AgentLoop's tool registry
    # MCP tools will be registered during lifespan startup, before first request
    package_skills_root = Path(__file__).resolve().parent.parent / "skills"
    workspace_skills_root = workspace / "skills"
    planner = SkillDrivenPlanner(
        skill_roots=[workspace_skills_root, package_skills_root],
        tool_registry=agent_loop.tools,  # Use AgentLoop's registry
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
            agent_loop=agent_loop,
        )
    )
    return app
