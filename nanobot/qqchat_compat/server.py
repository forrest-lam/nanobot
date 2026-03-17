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
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
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
        log_prompts=config.log_prompts,  # Pass log_prompts config
    )
    
    # Hold MCP stack reference for cleanup
    mcp_stack_holder: dict[str, Any] = {"stack": None}
    
    # Log at module level to confirm function is defined
    logger.warning("QQChat compat: Defining lifespan function (this should appear once)")
    
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage MCP connections lifecycle - connect BEFORE any requests."""
        import sys
        # Force output to stderr to bypass any logging config
        sys.stderr.write("\n" + "=" * 80 + "\n")
        sys.stderr.write("🚀🚀🚀 LIFESPAN STARTUP TRIGGERED! 🚀🚀🚀\n")
        sys.stderr.write("=" * 80 + "\n")
        sys.stderr.flush()
        logger.info("QQChat compat: Lifespan startup BEGIN")
        logger.debug("QQChat compat: tools_config={}, mcp_servers={}", 
                     tools_config is not None, 
                     getattr(tools_config, 'mcp_servers', None) if tools_config else None)
        
        # Startup: Connect MCP servers BEFORE planner can try to activate skills
        if tools_config and tools_config.mcp_servers:
            from nanobot.agent.tools.mcp import connect_mcp_servers
            
            logger.info("QQChat compat: Connecting {} MCP servers...", len(tools_config.mcp_servers))
            
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
                
                logger.info("QQChat compat: MCP servers connected, {} tools registered",
                           len(agent_loop.tools.tool_names))
                
                # Enable tools that are in the whitelist
                enabled_count = 0
                for tool_name in allowed_tools:
                    count = agent_loop.tools.enable(tool_name)
                    if count > 0:
                        enabled_count += 1
                        logger.debug("QQChat compat: Enabled tool '{}'", tool_name)
                
                logger.info(
                    "QQChat compat: MCP setup complete, {} tools total, {} enabled",
                    len(agent_loop.tools.tool_names), enabled_count
                )
            except Exception as e:
                import traceback
                logger.error("QQChat compat: Failed to connect MCP servers: {}\n{}", 
                            e, traceback.format_exc())
                if mcp_stack_holder["stack"]:
                    try:
                        await mcp_stack_holder["stack"].aclose()
                    except Exception:
                        pass
                    mcp_stack_holder["stack"] = None
        else:
            logger.warning("QQChat compat: No MCP servers configured (tools_config={}, mcp_servers={})",
                          tools_config is not None,
                          getattr(tools_config, 'mcp_servers', None) if tools_config else None)
        
        logger.info("QQChat compat: Lifespan startup COMPLETE, yielding to app...")
        yield  # Application runs
        
        # Shutdown: Close MCP connections
        logger.info("QQChat compat: Lifespan shutdown BEGIN")
        if mcp_stack_holder["stack"]:
            try:
                await mcp_stack_holder["stack"].aclose()
                logger.info("QQChat compat: MCP connections closed")
            except Exception as e:
                logger.warning("QQChat compat: Error closing MCP connections: {}", e)
        logger.info("QQChat compat: Lifespan shutdown COMPLETE")
    
    logger.warning("QQChat compat: Creating FastAPI app with lifespan (lifespan function id: {})", id(lifespan))
    app = FastAPI(title="QQ Chat Assistant API", lifespan=lifespan)
    
    # Add custom exception handler for validation errors
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc: RequestValidationError):
        """Custom handler to detect common client mistakes in /init request."""
        # Check if this is an /init request with wrong available_mcp_tools format
        if request.url.path == "/init" or request.url.path.endswith("/init"):
            # Print raw request body for debugging
            try:
                body = await request.json()
                logger.info("=== /init request body ===")
                logger.info("user_uin: {}", body.get("user_uin"))
                logger.info("session_id: {}", body.get("session_id"))
                logger.info("user_uid: {}", body.get("user_uid"))
                logger.info("user_nick: {}", body.get("user_nick"))
                logger.info("client_version: {}", body.get("client_version"))
                logger.info("available_mcp_tools type: {}", type(body.get("available_mcp_tools")))
                logger.info("available_mcp_tools length: {}", len(body.get("available_mcp_tools", [])))
                if body.get("available_mcp_tools"):
                    logger.info("available_mcp_tools[0] type: {}", type(body["available_mcp_tools"][0]))
                    logger.info("available_mcp_tools[0] content: {}", body["available_mcp_tools"][0])
                logger.info("mcp_tool_schemas length: {}", len(body.get("mcp_tool_schemas", [])))
                logger.info("========================")
            except Exception as e:
                logger.error("Failed to parse request body: {}", e)
            
            for error in exc.errors():
                # Detect: client sent objects instead of strings in available_mcp_tools
                if (error.get("loc") and len(error["loc"]) >= 2 
                    and error["loc"][1] == "available_mcp_tools"
                    and error["type"] == "string_type"):
                    
                    logger.error(
                        "Client sent tool objects to 'available_mcp_tools' field (expects string array). "
                        "Client should use 'mcp_tool_schemas' field for full tool definitions. "
                        "Original error: {}", error
                    )
                    
                    return JSONResponse(
                        status_code=422,
                        content={
                            "status": "error",
                            "error": (
                                "字段 'available_mcp_tools' 应为工具名称数组 (如 ['search_chats', 'get_profiles'])。"
                                "请使用 'mcp_tool_schemas' 字段传递完整的工具定义(包含 name, description, inputSchema)。"
                                "\n\n"
                                "示例:\n"
                                "{\n"
                                '  "user_uin": "...",\n'
                                '  "mcp_tool_schemas": [\n'
                                "    {\n"
                                '      "name": "search_chats",\n'
                                '      "description": "搜索会话",\n'
                                '      "input_schema": {\n'
                                '        "type": "object",\n'
                                '        "properties": {"keywords": {"type": "array"}},\n'
                                '        "required": ["keywords"]\n'
                                "      }\n"
                                "    }\n"
                                "  ]\n"
                                "}"
                            ),
                            "detail": exc.errors(),
                        }
                    )
        
        # For other validation errors, return standard FastAPI response
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors()}
        )

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
    # If enable_skills is False, don't create planner
    planner = None
    if config.enable_skills:
        package_skills_root = Path(__file__).resolve().parent.parent / "skills"
        workspace_skills_root = workspace / "skills"
        planner = SkillDrivenPlanner(
            skill_roots=[workspace_skills_root, package_skills_root],
            tool_registry=agent_loop.tools,  # Use AgentLoop's registry
            prompt_store=prompt_store,
        )
    else:
        logger.info("Skills disabled by config, all client tools will be enabled")

    app.include_router(
        create_router(
            session_store=session_store,
            memory_store=memory_store,
            prompt_store=prompt_store,
            user_config_store=user_config_store,
            planner=planner,
            policy=policy,
            agent_loop=agent_loop,
            enable_skills=config.enable_skills,
        )
    )
    logger.warning("QQChat compat: Returning FastAPI app (id: {})", id(app))
    return app
