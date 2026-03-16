"""HTTP routes for the ``qqchat_http`` channel.

All tool/skill restrictions defined in ``tool_policy`` and ``planner``
are scoped to this channel only.  Other nanobot channels are unaffected.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger

from nanobot.agent.loop import AgentLoop
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
)
from nanobot.qqchat_compat.session_store import SessionStore
from nanobot.qqchat_compat.tool_policy import ToolPolicy
from nanobot.qqchat_compat.user_config_store import UserConfigStore


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\\n\\n"


def create_router(
    *,
    session_store: SessionStore,
    memory_store: AccountMemoryStore,
    prompt_store: UserPromptStore,
    user_config_store: UserConfigStore,
    planner: SkillDrivenPlanner,
    policy: ToolPolicy,
    agent_loop: AgentLoop | None = None,
) -> APIRouter:
    router = APIRouter(tags=["qqchat-compat"])

    @router.post("/init", response_model=InitResponse)
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
        if planner.tool_registry:
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
            available_skills = planner.list_available_skills()
            enabled_skills = planner.list_enabled_skills(request.available_mcp_tools)
            
            # Get available and enabled MCP tools
            if planner.tool_registry:
                all_mcp_tools = [name for name, tool in planner.tool_registry.list_all() 
                                if getattr(tool, "mcp_server_name", None)]
                enabled_mcp_tools = [name for name in all_mcp_tools 
                                    if planner.tool_registry.is_enabled(name)]
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
                        yield _sse_event({"type": "answer_start"})
                        
                        # Get or create session
                        agent_session = agent_loop.sessions.get_or_create(session_key)
                        
                        # Build initial messages
                        history = agent_session.get_history(max_messages=0)
                        initial_messages = agent_loop.context.build_messages(
                            history=history,
                            current_message=msg.content,
                            media=msg.media if msg.media else None,
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                        )
                        
                        # Progress handler to send tool hints to client
                        async def _progress_handler(content: str, *, tool_hint: bool = False) -> None:
                            if tool_hint:
                                # Send progress hint as SSE event
                                # Note: Can't yield here, will collect and send in main loop
                                pass
                        
                        accumulated_answer = ""
                        final_messages = []
                        
                        # Stream from agent loop
                        async for chunk, is_final, messages in agent_loop._run_agent_loop_stream(
                            initial_messages, on_progress=_progress_handler
                        ):
                            if chunk:
                                accumulated_answer += chunk
                                # logger.debug("Yielding SSE chunk: {} chars", len(chunk))
                                yield _sse_event({"type": "answer_chunk", "content": chunk})
                                # Add tiny delay to ensure chunk is sent immediately
                                await asyncio.sleep(0)
                            
                            if is_final:
                                final_messages = messages
                                break
                        
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

        hint = planner.build_progress_hint(allowed_calls)
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
            else:
                print(f"  Error:   {result.get('error', 'Unknown error')}")
        print(f"{'='*60}\n")
        
        session = session_store.get(request.user_uin, request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        session.search_results.extend(request.search_results)

        final_answer = planner.summarize_results(
            session.query,
            session.search_results,
            user_uin=session.user_uin,
        )
        if final_answer.startswith("暂未检索") or final_answer.startswith("检索已完成，但结果为空"):
            follow_up = planner.plan_follow_up(session.round_count, session.query)
            follow_calls = [c for c in planner.build_calls(follow_up, session.round_count) if policy.is_allowed(c.tool)]
            if follow_calls:
                session.pending_calls = follow_calls
                session.status = "need_search"
                session.round_count += 1
                session_store.save(session)
                follow_hint = planner.build_progress_hint(follow_calls, is_follow_up=True)
                resp = CompatResponse(
                    status="need_search",
                    need_search=True,
                    session_id=request.session_id,
                    user_uin=request.user_uin,
                    mcp_calls=follow_calls,
                    progress_hint=follow_hint,
                )
                
                # Print follow-up search response
                print(f"\n{'='*60}")
                print(f"🔄 Submit Search Results Response (Follow-up):")
                print(f"{'='*60}")
                print(f"Status:         need_search")
                print(f"Round Count:    {session.round_count}")
                print(f"Progress Hint:  {follow_hint}")
                print(f"Follow-up Calls ({len(follow_calls)}):")
                for call in follow_calls:
                    print(f"  - {call.tool}({json.dumps(call.arguments, ensure_ascii=False)})")
                print(f"{'='*60}\n")
                
                if request.stream:
                    async def _ns_stream() -> AsyncGenerator[str, None]:
                        yield _sse_event({
                            "type": "need_search",
                            "progress_hint": follow_hint,
                            "mcp_calls": [c.model_dump() for c in follow_calls],
                        })
                        yield _sse_event({"type": "done"})
                    return StreamingResponse(_ns_stream(), media_type="text/event-stream")
                return resp

        session.status = "final_answer"
        session.pending_calls = []
        session_store.save(session)

        memory_store.append_record(
            user_uin=session.user_uin,
            query=session.query,
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

        # Print final answer response
        print(f"\n{'='*60}")
        print(f"✅ Submit Search Results Response (Final Answer):")
        print(f"{'='*60}")
        print(f"Status:       final_answer")
        print(f"Round Count:  {session.round_count}")
        answer_preview = final_answer[:300] if len(final_answer) > 300 else final_answer
        print(f"Answer:       {answer_preview}{'...' if len(final_answer) > 300 else ''}")
        print(f"{'='*60}\n")

        if request.stream:
            async def _fa_stream() -> AsyncGenerator[str, None]:
                yield _sse_event({"type": "answer_start"})
                # Stream answer in chunks (simulate typing effect)
                chunk_size = 50  # Characters per chunk
                for i in range(0, len(final_answer), chunk_size):
                    chunk = final_answer[i:i+chunk_size]
                    yield _sse_event({"type": "answer_chunk", "content": chunk})
                yield _sse_event({"type": "done"})
            return StreamingResponse(_fa_stream(), media_type="text/event-stream")

        return resp

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
    async def update_user_prompt(user_uin: str, prompt_name: str, payload: dict):
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
    async def add_personality_trait(user_uin: str, payload: dict):
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
