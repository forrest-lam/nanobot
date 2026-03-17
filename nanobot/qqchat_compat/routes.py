"""HTTP routes for the ``qqchat_http`` channel.

All tool/skill restrictions defined in ``tool_policy`` and ``planner``
are scoped to this channel only.  Other nanobot channels are unaffected.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.base import Tool
from nanobot.qqchat_compat._channel import CHANNEL
from nanobot.qqchat_compat.memory_store import AccountMemoryStore
from nanobot.qqchat_compat.planner import SkillDrivenPlanner
from nanobot.qqchat_compat.prompt_store import UserPromptStore
from nanobot.qqchat_compat.schemas import (
    CompatResponse,
    InitRequest,
    InitResponse,
    QueryRequest,
    SearchResultRequest,
    SessionSnapshot,
    ToolCall,
)
from nanobot.qqchat_compat.session_store import SessionStore
from nanobot.qqchat_compat.tool_policy import ToolPolicy
from nanobot.qqchat_compat.user_config_store import UserConfigStore


class ClientProvidedTool(Tool):
    """Placeholder for client-provided MCP tools (search_chats, get_profiles, etc)."""
    
    def __init__(self, name: str, description: str = "", input_schema: dict[str, Any] | None = None):
        self._name: str = name
        self._description: str = description or f"Client-provided tool: {name}"
        self._input_schema: dict[str, Any] = input_schema or {"type": "object", "properties": {}, "required": []}
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def description(self) -> str:
        return self._description
    
    @property
    def parameters(self) -> dict[str, Any]:
        """Return the input_schema for OpenAI function calling format."""
        return self._input_schema
    
    @property
    def input_schema(self) -> dict[str, Any]:
        return self._input_schema
    
    async def execute(self, **kwargs: Any) -> Any:
        raise NotImplementedError(f"Tool {self._name} should be handled by client")
    
    async def run(self, **kwargs: Any) -> Any:
        raise NotImplementedError(f"Tool {self._name} should be handled by client")


def _sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\\n\\n"


def create_router(
    *,
    session_store: SessionStore,
    memory_store: AccountMemoryStore,
    prompt_store: UserPromptStore,
    user_config_store: UserConfigStore,
    planner: SkillDrivenPlanner | None,
    policy: ToolPolicy,
    agent_loop: AgentLoop | None = None,
    enable_skills: bool = False,
) -> APIRouter:
    router = APIRouter(tags=["qqchat-compat"])

    @router.post("/init")
    async def init(request: InitRequest) -> InitResponse:
        """Initialize client connection and save user configuration.
        
        This endpoint should be called when the client first connects or session starts.
        It saves:
        1. User's QQ identity (UIN, UID, nickname)
        2. Session ID for this conversation
        3. Available MCP tools from the client
        4. Client version and metadata
        
        The stored configuration is used to:
        - Populate USER.md template with identity info
        - Enable/disable skills based on available tools
        - Track client capabilities for future features
        - Initialize session for the conversation
        """
        # Log detailed request content
        logger.info("=== /init request received (validated) ===")
        logger.info("user_uin: {}", request.user_uin)
        logger.info("session_id: {}", request.session_id)
        logger.info("user_uid: {}", request.user_uid)
        logger.info("user_nick: {}", request.user_nick)
        logger.info("client_version: {}", request.client_version)
        logger.info("available_mcp_tools: {} (type: {})", request.available_mcp_tools, type(request.available_mcp_tools))
        logger.info("mcp_tool_schemas: {} schemas", len(request.mcp_tool_schemas))
        for i, schema in enumerate(request.mcp_tool_schemas):
            logger.info("  [{}] name: {}, has_description: {}, has_input_schema: {}", 
                       i, schema.name, bool(schema.description), bool(schema.input_schema))
        logger.info("client_metadata: {}", request.client_metadata)
        logger.info("=========================================")
        
        # Print client init parameters
        print(f"\n{'='*60}")
        print(f"🚀 Client Init Request Received:")
        print(f"{'='*60}")
        print(f"User UIN:         {request.user_uin}")
        print(f"User UID:         {request.user_uid or 'N/A'}")
        print(f"User Nick:        {request.user_nick or 'N/A'}")
        print(f"Session ID:       {request.session_id}")
        print(f"Client Version:   {request.client_version or 'N/A'}")
        if request.client_metadata:
            print(f"Client Metadata:  {json.dumps(request.client_metadata, ensure_ascii=False, indent=2)}")
        if request.available_mcp_tools:
            print(f"Available MCP Tools ({len(request.available_mcp_tools)}):")
            for tool in request.available_mcp_tools:
                print(f"  - {tool}")
        else:
            print(f"Available MCP Tools: None")
        
        # Debug: Check which tools are registered in the registry
        if planner and planner.tool_registry:
            registered_tools = planner.tool_registry.tool_names
            print(f"\nRegistered tools in registry ({len(registered_tools)}):")
            for tool in registered_tools:
                enabled = planner.tool_registry.is_enabled(tool)
                status = "✓" if enabled else "✗"
                print(f"  {status} {tool}")
        
        print(f"{'='*60}\n")
        
        try:
            # Update user configuration
            config = user_config_store.update(
                user_uin=request.user_uin,
                user_uid=request.user_uid,
                user_nick=request.user_nick,
                available_mcp_tools=request.available_mcp_tools,
                client_version=request.client_version,
                client_metadata=request.client_metadata,
            )
            
            # Register client-provided MCP tools as placeholder tools
            # If enable_skills=False (planner is None), register tools directly to agent_loop.tools and enable them
            # If enable_skills=True (planner exists), register to planner's registry but keep disabled (skill-driven)
            tool_registry = agent_loop.tools if planner is None else planner.tool_registry
            enable_all_tools = planner is None  # Enable all tools when skills are disabled
            
            if tool_registry:
                # Priority 1: Use mcp_tool_schemas if provided (contains full schema)
                if request.mcp_tool_schemas:
                    for tool_schema in request.mcp_tool_schemas:
                        if tool_schema.name not in tool_registry.tool_names:
                            client_tool = ClientProvidedTool(
                                name=tool_schema.name,
                                description=tool_schema.description,
                                input_schema=tool_schema.input_schema,
                            )
                            tool_registry.register(client_tool, enabled=enable_all_tools)
                            logger.debug(
                                f"Registered client-provided tool '{tool_schema.name}' with full schema "
                                f"(enabled={enable_all_tools}, properties: {list(tool_schema.input_schema.get('properties', {}).keys())})"
                            )
                # Priority 2: Fallback to available_mcp_tools (legacy, name-only)
                elif request.available_mcp_tools:
                    for tool_name in request.available_mcp_tools:
                        if tool_name not in tool_registry.tool_names:
                            client_tool = ClientProvidedTool(tool_name)
                            tool_registry.register(client_tool, enabled=enable_all_tools)
                            logger.warning(
                                f"Registered client-provided tool '{tool_name}' WITHOUT schema "
                                f"(enabled={enable_all_tools}, client should use mcp_tool_schemas field instead)"
                            )
            
            # Initialize session for this conversation
            session = session_store.get_or_create(request.user_uin, request.session_id)
            
            # Initialize user's prompt files with identity
            prompt_store.get_prompt(
                request.user_uin,
                "USER.md",
                user_uid=request.user_uid,
                user_nick=request.user_nick,
            )
            
            # Get available and enabled skills
            available_skills = []
            enabled_skills = []
            if planner:
                available_skills = planner.list_available_skills()
                enabled_skills = planner.list_enabled_skills(request.available_mcp_tools)
            
            # Get available and enabled MCP tools
            if planner and planner.tool_registry:
                all_mcp_tools = [name for name, tool in planner.tool_registry.list_all() 
                                if getattr(tool, "mcp_server_name", None)]
                enabled_mcp_tools = [name for name in all_mcp_tools 
                                    if planner.tool_registry.is_enabled(name)]
            elif agent_loop:
                # If no planner (skills disabled), enable all client tools
                all_mcp_tools = [name for name, tool in agent_loop.tools.list_all() 
                                if getattr(tool, "mcp_server_name", None)]
                # Enable all client-provided tools
                for tool_name in request.available_mcp_tools:
                    if tool_name in agent_loop.tools.tool_names:
                        agent_loop.tools.enable(tool_name)
                        logger.info("Enabled client tool (no skills): {}", tool_name)
                enabled_mcp_tools = [name for name in all_mcp_tools 
                                    if agent_loop.tools.is_enabled(name)]
            else:
                all_mcp_tools = []
                enabled_mcp_tools = []
            
            return InitResponse(
                status="success",
                message=f"用户初始化成功: {request.user_nick or request.user_uin}",
                user_uin=request.user_uin,
                user_identity_initialized=True,
                available_skills=available_skills,
                enabled_skills=enabled_skills,
                available_mcp_tools=all_mcp_tools,
                enabled_mcp_tools=enabled_mcp_tools,
            )
            
        except Exception as e:
            return InitResponse(
                status="error",
                message="初始化失败",
                user_uin=request.user_uin,
                error=str(e),
            )

    @router.post("/query")
    async def query(request: QueryRequest):
        # Print query request parameters
        print(f"\n{'='*60}")
        print(f"🔍 Query Request Received:")
        print(f"{'='*60}")
        print(f"User UIN:      {request.user_uin}")
        print(f"Session ID:    {request.session_id}")
        print(f"Query:         {request.query}")
        print(f"Current Time:  {request.current_time or 'N/A'}")
        print(f"Stream:        {request.stream}")
        if request.user_uid:
            print(f"User UID:      {request.user_uid}")
        if request.user_nick:
            print(f"User Nick:     {request.user_nick}")
        print(f"{'='*60}\n")
        
        session = session_store.get_or_create(request.user_uin, request.session_id)
        session.query = request.query
        session.current_time = request.current_time
        
        # Initialize user's prompts with identity info if first access
        prompt_store.get_prompt(
            request.user_uin,
            "USER.md",
            user_uid=request.user_uid,
            user_nick=request.user_nick,
        )

        # Pre-activate skills based on query keywords using planner
        # This ensures MCP tools are enabled before LLM tool selection
        if planner:
            planned = planner.plan_initial(request.query)
            # This will activate skills and enable their MCP tools
            logger.info("Pre-activated skills for query: '{}'", request.query[:50])

        # Use AgentLoop if available, otherwise fallback to planner
        if agent_loop is not None:
            # Use LLM-based AgentLoop
            from nanobot.bus.events import InboundMessage
            
            # Build session key for AgentLoop
            session_key = f"qqchat_http:{request.user_uin}:{request.session_id}"
            
            # Check if this is a continuation (tools already executed, waiting for answer)
            if hasattr(session, 'agent_waiting_answer') and session.agent_waiting_answer:
                # Continue processing to get final answer
                session.agent_waiting_answer = False
                session_store.save(session)
                
                # Process with AgentLoop to get final answer
                msg = InboundMessage(
                    channel=CHANNEL,
                    sender_id=request.user_uin,
                    chat_id=request.session_id,
                    content=request.query,
                    metadata={"current_time": request.current_time},
                )
                
                try:
                    response_msg = await asyncio.wait_for(
                        agent_loop._process_message(msg, session_key=session_key),
                        timeout=45.0
                    )
                except asyncio.TimeoutError:
                    error_resp = CompatResponse(
                        status="error",
                        session_id=request.session_id,
                        user_uin=request.user_uin,
                        error="处理超时(45秒),请简化问题或稍后重试。",
                    )
                    if request.stream:
                        async def _timeout_stream() -> AsyncGenerator[str, None]:
                            yield _sse_event({"type": "error", "message": error_resp.error})
                        return StreamingResponse(_timeout_stream(), media_type="text/event-stream")
                    return error_resp
                
                if response_msg is None:
                    error_resp = CompatResponse(
                        status="error",
                        session_id=request.session_id,
                        user_uin=request.user_uin,
                        error="处理失败，请重试。",
                    )
                    if request.stream:
                        async def _err_stream() -> AsyncGenerator[str, None]:
                            yield _sse_event({"type": "error", "message": error_resp.error})
                        return StreamingResponse(_err_stream(), media_type="text/event-stream")
                    return error_resp
                
                final_answer = response_msg.content
                
                memory_store.append_record(
                    user_uin=request.user_uin,
                    query=request.query,
                    answer=final_answer,
                    round_count=session.round_count,
                )
                
                resp = CompatResponse(
                    status="final_answer",
                    need_search=False,
                    session_id=request.session_id,
                    user_uin=request.user_uin,
                    final_answer=final_answer,
                )
                
                print(f"\n{'='*60}")
                print(f"✅ Query Response (Final Answer from Agent Loop):")
                print(f"{'='*60}")
                print(f"Status:       final_answer")
                answer_preview = final_answer[:300] if len(final_answer) > 300 else final_answer
                print(f"Answer:       {answer_preview}{'...' if len(final_answer) > 300 else ''}")
                print(f"{'='*60}\n")
                
                if request.stream:
                    async def _fa_stream() -> AsyncGenerator[str, None]:
                        yield _sse_event({"type": "answer_start"})
                        # Stream answer in chunks
                        chunk_size = 50
                        for i in range(0, len(final_answer), chunk_size):
                            chunk = final_answer[i:i+chunk_size]
                            yield _sse_event({"type": "answer_chunk", "content": chunk})
                        yield _sse_event({"type": "done"})
                    return StreamingResponse(_fa_stream(), media_type="text/event-stream")
                
                return resp
            
            # First request: start processing
            # Create inbound message
            msg = InboundMessage(
                channel=CHANNEL,
                sender_id=request.user_uin,
                chat_id=request.session_id,
                content=request.query,
                metadata={"current_time": request.current_time},
            )
            
            # Use streaming mode if requested
            if request.stream:
                if agent_loop is None:
                    async def _error_stream() -> AsyncGenerator[str, None]:
                        yield _sse_event({"type": "error", "message": "Agent loop not initialized"})
                    return StreamingResponse(_error_stream(), media_type="text/event-stream")
                
                async def _real_stream() -> AsyncGenerator[str, None]:
                    try:
                        # Get or create session
                        agent_session = agent_loop.sessions.get_or_create(session_key)
                        
                        # Build initial messages (use request.query directly)
                        history = agent_session.get_history(max_messages=0)
                        initial_messages = agent_loop.context.build_messages(
                            history=history,
                            current_message=request.query,
                            media=None,
                            channel=CHANNEL,
                            chat_id=request.session_id,
                            enable_skills=enable_skills,
                        )
                        
                        # Track pending MCP tool calls
                        pending_mcp_calls: list[ToolCall] = []
                        
                        # Progress handler to send tool hints to client
                        async def _progress_handler(content: str, *, tool_hint: bool = False) -> None:
                            if tool_hint:
                                # Send progress hint as SSE event
                                # Note: Can't yield here, will collect and send in main loop
                                pass
                        
                        accumulated_answer = ""
                        final_messages = []
                        need_client_execution = False
                        answer_started = False  # Track whether answer_start has been sent
                        
                        # Stream from agent loop
                        async for chunk, is_final, messages, tool_calls_info in agent_loop._run_agent_loop_stream(
                            initial_messages, on_progress=_progress_handler
                        ):
                            # Check if this chunk contains client tool calls
                            if tool_calls_info:
                                # Extract thinking/reasoning for the reason field
                                reason_text = ""
                                for msg in reversed(messages):
                                    if msg.get("role") == "assistant":
                                        # Check for thinking_blocks (Anthropic extended thinking)
                                        if thinking_blocks := msg.get("thinking_blocks"):
                                            reason_parts = []
                                            for block in thinking_blocks:
                                                if isinstance(block, dict) and block.get("type") == "thinking":
                                                    if text := block.get("thinking"):
                                                        reason_parts.append(text)
                                            if reason_parts:
                                                reason_text = "\n".join(reason_parts)
                                        # Check for reasoning_content (Kimi, DeepSeek-R1 etc.)
                                        elif reasoning := msg.get("reasoning_content"):
                                            reason_text = reasoning
                                        # Fallback: use text content before tool_calls
                                        elif content := msg.get("content"):
                                            if isinstance(content, str) and content.strip():
                                                reason_text = content.strip()
                                        break
                                
                                client_calls = []
                                for tc in tool_calls_info:
                                    tool_obj = agent_loop.tools.get(tc['name'])
                                    if tool_obj and isinstance(tool_obj, ClientProvidedTool):
                                        client_calls.append(ToolCall(
                                            tool=tc['name'],
                                            arguments=tc.get('arguments', {}),
                                            reason=reason_text,
                                        ))
                                
                                if client_calls:
                                    # Found client tool calls, need to pause and wait for client
                                    pending_mcp_calls = client_calls
                                    need_client_execution = True
                                    final_messages = messages
                                    
                                    # Send mcp_calls to client (without answer_start)
                                    yield _sse_event({
                                        "type": "need_search",
                                        "mcp_calls": [c.model_dump() for c in client_calls],
                                        "progress_hint": SkillDrivenPlanner.build_progress_hint(client_calls),
                                    })
                                    
                                    logger.info("Sent {} MCP tool calls to client, waiting for results", len(client_calls))
                                    break
                            
                            if chunk:
                                # Send answer_start only once before first chunk
                                if not answer_started:
                                    yield _sse_event({"type": "answer_start"})
                                    answer_started = True
                                
                                accumulated_answer += chunk
                                # logger.debug("Yielding SSE chunk: {} chars", len(chunk))
                                yield _sse_event({"type": "answer_chunk", "content": chunk})
                                # Add tiny delay to ensure chunk is sent immediately
                                await asyncio.sleep(0)
                            
                            if is_final:
                                final_messages = messages
                                break
                        
                        if need_client_execution:
                            # Update session to wait for client results
                            session.pending_calls = pending_mcp_calls
                            session.status = "need_search"
                            session.round_count += 1
                            # Store partial messages for resumption
                            session.partial_messages = final_messages
                            session_store.save(session)
                            
                            # Also save to agent session
                            agent_loop._save_turn(agent_session, final_messages, 1 + len(history))
                            agent_loop.sessions.save(agent_session)
                            
                            yield _sse_event({"type": "done"})
                        else:
                            # Normal completion
                            # Save turn to session
                            agent_loop._save_turn(agent_session, final_messages, 1 + len(history))
                            agent_loop.sessions.save(agent_session)
                            
                            # Save to memory store
                            memory_store.append_record(
                                user_uin=request.user_uin,
                                query=request.query,
                                answer=accumulated_answer,
                                round_count=session.round_count,
                            )
                            
                            yield _sse_event({"type": "done"})
                        
                    except asyncio.TimeoutError:
                        yield _sse_event({"type": "error", "message": "处理超时(45秒),请简化问题或稍后重试。"})
                    except Exception as e:
                        logger.exception("Error in streaming mode")
                        yield _sse_event({"type": "error", "message": f"处理失败: {str(e)}"})
                
                return StreamingResponse(_real_stream(), media_type="text/event-stream")
            
            # Non-streaming mode (original logic)
            # Get or create agent session
            agent_session = agent_loop.sessions.get_or_create(session_key)
            
            # Build initial messages
            history = agent_session.get_history(max_messages=0)
            initial_messages = agent_loop.context.build_messages(
                history=history,
                current_message=msg.content,
                media=msg.media if msg.media else None,
                channel=msg.channel,
                chat_id=msg.chat_id,
                enable_skills=enable_skills,
            )
            
            # Track tool calls for progress hints
            tool_call_hints: list[str] = []
            
            # Progress handler to capture tool hints
            async def _progress_handler(content: str, *, tool_hint: bool = False) -> None:
                if tool_hint:
                    tool_call_hints.append(content)
            
            # Run agent loop with progress tracking
            try:
                accumulated_answer = ""
                final_messages = []
                
                async for chunk, is_final, messages in agent_loop._run_agent_loop_stream(
                    initial_messages, on_progress=_progress_handler
                ):
                    if chunk:
                        accumulated_answer += chunk
                    
                    if is_final:
                        final_messages = messages
                        break
                
                # If there were tool calls but no answer yet, return need_search with progress hint
                if tool_call_hints and not accumulated_answer:
                    # Build progress hint from tool calls
                    progress_hint = " → ".join(tool_call_hints) if tool_call_hints else "正在处理工具调用..."
                    
                    # Save session state and mark waiting for answer
                    agent_loop._save_turn(agent_session, final_messages, 1 + len(history))
                    agent_loop.sessions.save(agent_session)
                    
                    # Set flag for continuation
                    session.agent_waiting_answer = True
                    session_store.save(session)
                    
                    # Return need_search status with empty mcp_calls
                    resp = CompatResponse(
                        status="need_search",
                        need_search=False,  # False because tools executed server-side
                        session_id=request.session_id,
                        user_uin=request.user_uin,
                        progress_hint=progress_hint,
                        mcp_calls=[],  # Empty because tools already executed
                    )
                    
                    print(f"\n{'='*60}")
                    print(f"🔄 Query Response (Tool Execution - Need Search False):")
                    print(f"{'='*60}")
                    print(f"Status:        need_search")
                    print(f"Need Search:   False (tools executed server-side)")
                    print(f"Progress Hint: {progress_hint}")
                    print(f"MCP Calls:     [] (empty)")
                    print(f"{'='*60}\n")
                    
                    return resp
                
                # Normal completion with answer
                final_answer = accumulated_answer
                
            except asyncio.TimeoutError:
                error_resp = CompatResponse(
                    status="error",
                    session_id=request.session_id,
                    user_uin=request.user_uin,
                    error="处理超时(45秒),请简化问题或稍后重试。",
                )
                if request.stream:
                    async def _timeout_stream() -> AsyncGenerator[str, None]:
                        yield _sse_event({"type": "error", "message": error_resp.error})
                    return StreamingResponse(_timeout_stream(), media_type="text/event-stream")
                return error_resp
            except Exception as e:
                logger.exception("Error in agent loop processing")
                error_resp = CompatResponse(
                    status="error",
                    session_id=request.session_id,
                    user_uin=request.user_uin,
                    error=f"处理失败: {str(e)}",
                )
                if request.stream:
                    async def _err_stream() -> AsyncGenerator[str, None]:
                        yield _sse_event({"type": "error", "message": error_resp.error})
                    return StreamingResponse(_err_stream(), media_type="text/event-stream")
                return error_resp
            
            if not final_answer:
                error_resp = CompatResponse(
                    status="error",
                    session_id=request.session_id,
                    user_uin=request.user_uin,
                    error="处理失败，请重试。",
                )
                if request.stream:
                    async def _err_stream() -> AsyncGenerator[str, None]:
                        yield _sse_event({"type": "error", "message": error_resp.error})
                    return StreamingResponse(_err_stream(), media_type="text/event-stream")
                return error_resp
            
            # Extract previous queries from current session history (not cross-session memory)
            current_session_queries: list[str] = []
            for msg in agent_session.messages:
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        current_session_queries.append(content.strip())
            
            memory_store.append_record(
                user_uin=request.user_uin,
                query=request.query,
                answer=final_answer,
                round_count=session.round_count,
            )
            
            resp = CompatResponse(
                status="final_answer",
                need_search=False,
                session_id=request.session_id,
                user_uin=request.user_uin,
                final_answer=final_answer,
            )
            
            print(f"\n{'='*60}")
            print(f"✅ Query Response (LLM-based):")
            print(f"{'='*60}")
            print(f"Status:       final_answer")
            answer_preview = final_answer[:300] if len(final_answer) > 300 else final_answer
            print(f"Answer:       {answer_preview}{'...' if len(final_answer) > 300 else ''}")
            print(f"{'='*60}\n")
            
            return resp
        
        # Fallback to rule-based planner
        planned = planner.plan_initial(request.query)
        calls = planner.build_calls(planned, session.round_count)
        allowed_calls = [call for call in calls if policy.is_allowed(call.tool)]

        if not allowed_calls:
            error_resp = CompatResponse(
                status="error",
                session_id=request.session_id,
                user_uin=request.user_uin,
                error="当前上下文无可用检索工具（已被策略限制）。",
            )
            
            # Print error response
            print(f"\n{'='*60}")
            print(f"❌ Query Response (Error):")
            print(f"{'='*60}")
            print(f"Status:  error")
            print(f"Message: {error_resp.error}")
            print(f"{'='*60}\n")
            
            if request.stream:
                async def _err_stream() -> AsyncGenerator[str, None]:
                    yield _sse_event({"type": "error", "message": error_resp.error})
                return StreamingResponse(_err_stream(), media_type="text/event-stream")
            return error_resp

        session.pending_calls = allowed_calls
        session.status = "need_search"
        session.round_count += 1
        session_store.save(session)

        hint = SkillDrivenPlanner.build_progress_hint(allowed_calls)
        response = CompatResponse(
            status="need_search",
            need_search=True,
            session_id=request.session_id,
            user_uin=request.user_uin,
            mcp_calls=allowed_calls,
            progress_hint=hint,
        )

        # Print success response
        print(f"\n{'='*60}")
        print(f"✅ Query Response (Need Search):")
        print(f"{'='*60}")
        print(f"Status:         need_search")
        print(f"Round Count:    {session.round_count}")
        print(f"Progress Hint:  {hint}")
        print(f"MCP Calls ({len(allowed_calls)}):")
        for call in allowed_calls:
            print(f"  - {call.tool}({json.dumps(call.arguments, ensure_ascii=False)})")
        print(f"{'='*60}\n")

        if request.stream:
            async def _stream() -> AsyncGenerator[str, None]:
                yield _sse_event({
                    "type": "need_search",
                    "progress_hint": hint,
                    "mcp_calls": [c.model_dump() for c in allowed_calls],
                })
                yield _sse_event({"type": "done"})
            return StreamingResponse(_stream(), media_type="text/event-stream")

        return response

    @router.post("/submit_search_results")
    async def submit_search_results(request: SearchResultRequest):
        # Print submit search results request
        print(f"\n{'='*60}")
        print(f"📥 Submit Search Results Request:")
        print(f"{'='*60}")
        print(f"User UIN:       {request.user_uin}")
        print(f"Session ID:     {request.session_id}")
        print(f"Results Count:  {len(request.search_results)}")
        print(f"Stream:         {request.stream}")
        for i, result in enumerate(request.search_results, 1):
            print(f"\nResult {i}:")
            print(f"  Tool:    {result.get('tool', 'N/A')}")
            print(f"  Success: {result.get('success', False)}")
            if result.get('success'):
                content = result.get('content', '')
                content_preview = content[:200] if len(content) > 200 else content
                print(f"  Content: {content_preview}{'...' if len(content) > 200 else ''}")
                # Log first 100 chars of content to loguru
                logger.debug("Tool '{}' result preview: {}", 
                           result.get('tool', 'N/A'), 
                           content[:100] if len(content) > 100 else content)
            else:
                print(f"  Error:   {result.get('error', 'Unknown error')}")
        print(f"{'='*60}\n")
        
        session = session_store.get(request.user_uin, request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if agent_loop is None:
            raise HTTPException(status_code=500, detail="Agent loop not initialized")

        # Get or create agent session
        session_key = f"{CHANNEL}_{request.user_uin}"
        agent_session = agent_loop.sessions.get_or_create(session_key)
        
        # Add tool results to messages
        messages = session.partial_messages or []
        if not messages:
            raise HTTPException(status_code=400, detail="No partial messages found in session")
        
        # Convert search results to tool results and add to messages
        for result in request.search_results:
            tool_name = result.get('tool')
            if not tool_name:
                logger.warning("Tool name is missing in result: {}", result)
                continue
            
            success = result.get('success', False)
            
            # Find matching tool call from pending_calls
            matching_call = None
            for call in session.pending_calls:
                if call.tool == tool_name:
                    matching_call = call
                    break
            
            if not matching_call:
                logger.warning("No matching pending call for tool: {}", tool_name)
                continue
            
            # Get tool call id from messages (last assistant message should have tool calls)
            tool_call_id = None
            for msg in reversed(messages):
                if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                    for tc in msg['tool_calls']:
                        if tc['function']['name'] == tool_name:
                            tool_call_id = tc['id']
                            break
                if tool_call_id:
                    break
            
            if not tool_call_id:
                logger.warning("No tool call id found for tool: {}", tool_name)
                tool_call_id = f"call_{tool_name}_{len(messages)}"  # Fallback
            
            # Add tool result to messages
            if success:
                # 优先使用 content,如果没有则尝试 result 或整个 result 对象
                content = result.get('content')
                if not content:
                    # 客户端可能使用了其他字段(如 result, message, total 等)
                    # 将整个 result 对象转为 JSON 字符串,但排除 tool 和 success 字段
                    import json
                    filtered_result = {k: v for k, v in result.items() if k not in ['tool', 'success']}
                    content = json.dumps(filtered_result, ensure_ascii=False)
                
                messages = agent_loop.context.add_tool_result(
                    messages, tool_call_id, tool_name, content
                )
            else:
                error = result.get('error', 'Tool execution failed')
                messages = agent_loop.context.add_tool_result(
                    messages, tool_call_id, tool_name, f"Error: {error}"
                )
        
        # Now continue agent loop with updated messages
        if request.stream:
            async def _continue_stream() -> AsyncGenerator[str, None]:
                try:
                    accumulated_answer = ""
                    answer_started = False
                    
                    # Continue streaming from agent loop
                    async for chunk, is_final, updated_messages, tool_calls_info in agent_loop._run_agent_loop_stream(
                        messages, on_progress=None
                    ):
                        # Check if need more client tools
                        if tool_calls_info:
                            # Extract thinking/reasoning for the reason field
                            reason_text = ""
                            for msg in reversed(updated_messages):
                                if msg.get("role") == "assistant":
                                    # Check for thinking_blocks (Anthropic extended thinking)
                                    if thinking_blocks := msg.get("thinking_blocks"):
                                        reason_parts = []
                                        for block in thinking_blocks:
                                            if isinstance(block, dict) and block.get("type") == "thinking":
                                                if text := block.get("thinking"):
                                                    reason_parts.append(text)
                                        if reason_parts:
                                            reason_text = "\n".join(reason_parts)
                                    # Check for reasoning_content (Kimi, DeepSeek-R1 etc.)
                                    elif reasoning := msg.get("reasoning_content"):
                                        reason_text = reasoning
                                    # Fallback: use text content before tool_calls
                                    elif content := msg.get("content"):
                                        if isinstance(content, str) and content.strip():
                                            reason_text = content.strip()
                                    break
                            
                            client_calls = []
                            for tc in tool_calls_info:
                                tool_obj = agent_loop.tools.get(tc['name'])
                                if tool_obj and isinstance(tool_obj, ClientProvidedTool):
                                    client_calls.append(ToolCall(
                                        tool=tc['name'],
                                        arguments=tc.get('arguments', {}),
                                        reason=reason_text,
                                    ))
                            
                            if client_calls:
                                # Need another round of client execution
                                session.pending_calls = client_calls
                                session.status = "need_search"
                                session.round_count += 1
                                session.partial_messages = updated_messages
                                session_store.save(session)
                                
                                yield _sse_event({
                                    "type": "need_search",
                                    "mcp_calls": [c.model_dump() for c in client_calls],
                                    "progress_hint": SkillDrivenPlanner.build_progress_hint(client_calls),
                                })
                                yield _sse_event({"type": "done"})
                                return
                        
                        if chunk:
                            # Send answer_start only once before first chunk
                            if not answer_started:
                                yield _sse_event({"type": "answer_start"})
                                answer_started = True
                            
                            accumulated_answer += chunk
                            yield _sse_event({"type": "answer_chunk", "content": chunk})
                            await asyncio.sleep(0)
                        
                        if is_final:
                            # Save to session and memory
                            agent_loop._save_turn(agent_session, updated_messages, len(agent_session.get_history()) + 1)
                            agent_loop.sessions.save(agent_session)
                            
                            memory_store.append_record(
                                user_uin=request.user_uin,
                                query=session.query,
                                answer=accumulated_answer,
                                round_count=session.round_count,
                            )
                            
                            session.status = "final_answer"
                            session.pending_calls = []
                            session_store.save(session)
                            
                            yield _sse_event({"type": "done"})
                            break
                            
                except Exception as e:
                    logger.exception("Error continuing agent loop")
                    yield _sse_event({"type": "error", "message": f"处理失败: {str(e)}"})
            
            return StreamingResponse(_continue_stream(), media_type="text/event-stream")
        
        # Non-streaming mode: fallback to simple summary
        session.search_results.extend(request.search_results)
        final_answer = planner.summarize_results(
            session.query,
            session.search_results,
            user_uin=session.user_uin,
        )
        
        session.status = "final_answer"
        session.pending_calls = []
        session_store.save(session)

        memory_store.append_record(
            user_uin=session.user_uin,
            query=session.query,
            answer=final_answer,
            round_count=session.round_count,
        )

        return CompatResponse(
            status="final_answer",
            need_search=False,
            session_id=request.session_id,
            user_uin=request.user_uin,
            final_answer=final_answer,
        )

    @router.get("/health")
    async def health():
        sessions = session_store.list_snapshots()
        return {"status": "ok", "channel": CHANNEL, "active_sessions": len(sessions)}

    @router.get("/session/{user_uin}/{session_id}")
    async def session_detail(user_uin: str, session_id: str):
        session = session_store.get(user_uin, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionSnapshot(
            key=session.key,
            user_uin=session.user_uin,
            session_id=session.session_id,
            status=session.status,
            round_count=session.round_count,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    @router.get("/prompt/{user_uin}")
    async def get_user_prompts(user_uin: str):
        """Get all prompt files for a user."""
        prompts = prompt_store.get_all_prompts(user_uin)
        return {"user_uin": user_uin, "prompts": prompts}

    @router.post("/prompt/{user_uin}/{prompt_name}")
    async def update_user_prompt(user_uin: str, prompt_name: str, payload: dict[str, Any]):
        """Update a specific prompt file.
        
        Body: {"content": "...", "append": false}
        """
        if prompt_name not in UserPromptStore.TEMPLATE_FILES:
            raise HTTPException(status_code=400, detail=f"Invalid prompt name: {prompt_name}")
        
        content = payload.get("content", "")
        append = payload.get("append", False)
        
        prompt_store.update_prompt(user_uin, prompt_name, content, append=append)
        return {"status": "ok", "user_uin": user_uin, "prompt_name": prompt_name}

    @router.post("/prompt/{user_uin}/personality")
    async def add_personality_trait(user_uin: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Add a personality trait to user's SOUL.md.
        
        Body: {"trait": "喜欢简洁的回答"}
        """
        trait = payload.get("trait", "").strip()
        if not trait:
            raise HTTPException(status_code=400, detail="Trait cannot be empty")
        
        prompt_store.append_personality(user_uin, trait)
        return {"status": "ok", "user_uin": user_uin, "trait": trait}

    @router.delete("/prompt/{user_uin}")
    async def reset_user_prompts(user_uin: str):
        """Reset all prompts to defaults."""
        prompt_store.delete_user_prompts(user_uin)
        return {"status": "ok", "user_uin": user_uin, "message": "Prompts reset to defaults"}

    return router
