"""HTTP routes for the ``qqchat_http`` channel.

All tool/skill restrictions defined in ``tool_policy`` and ``planner``
are scoped to this channel only.  Other nanobot channels are unaffected.
"""

from __future__ import annotations

import json
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from nanobot.qqchat_compat._channel import CHANNEL
from nanobot.qqchat_compat.memory_store import AccountMemoryStore
from nanobot.qqchat_compat.planner import SkillDrivenPlanner
from nanobot.qqchat_compat.schemas import CompatResponse, QueryRequest, SearchResultRequest, SessionSnapshot
from nanobot.qqchat_compat.session_store import SessionStore
from nanobot.qqchat_compat.tool_policy import ToolPolicy


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\\n\\n"


def create_router(
    *,
    session_store: SessionStore,
    memory_store: AccountMemoryStore,
    planner: SkillDrivenPlanner,
    policy: ToolPolicy,
) -> APIRouter:
    router = APIRouter(tags=["qqchat-compat"])

    @router.post("/query")
    async def query(request: QueryRequest):
        session = session_store.get_or_create(request.user_uin, request.session_id)
        session.query = request.query
        session.current_time = request.current_time

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
            if request.stream:
                async def _err_stream() -> AsyncGenerator[str, None]:
                    yield _sse_event({"type": "error", "message": error_resp.error})
                return StreamingResponse(_err_stream(), media_type="text/event-stream")
            return error_resp

        session.pending_calls = allowed_calls
        session.status = "need_search"
        session.round_count += 1
        session_store.save(session)

        response = CompatResponse(
            status="need_search",
            need_search=True,
            session_id=request.session_id,
            user_uin=request.user_uin,
            mcp_calls=allowed_calls,
        )

        if request.stream:
            async def _stream() -> AsyncGenerator[str, None]:
                yield _sse_event({"type": "need_search", "mcp_calls": [c.model_dump() for c in allowed_calls]})
                yield _sse_event({"type": "done"})
            return StreamingResponse(_stream(), media_type="text/event-stream")

        return response

    @router.post("/submit_search_results")
    async def submit_search_results(request: SearchResultRequest):
        session = session_store.get(request.user_uin, request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        session.search_results.extend(request.search_results)

        final_answer = planner.summarize_results(session.query, session.search_results)
        if final_answer.startswith("暂未检索") or final_answer.startswith("检索已完成，但结果为空"):
            follow_up = planner.plan_follow_up(session.round_count, session.query)
            follow_calls = [c for c in planner.build_calls(follow_up, session.round_count) if policy.is_allowed(c.tool)]
            if follow_calls:
                session.pending_calls = follow_calls
                session.status = "need_search"
                session.round_count += 1
                session_store.save(session)
                resp = CompatResponse(
                    status="need_search",
                    need_search=True,
                    session_id=request.session_id,
                    user_uin=request.user_uin,
                    mcp_calls=follow_calls,
                )
                if request.stream:
                    async def _ns_stream() -> AsyncGenerator[str, None]:
                        yield _sse_event({"type": "need_search", "mcp_calls": [c.model_dump() for c in follow_calls]})
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

        if request.stream:
            async def _fa_stream() -> AsyncGenerator[str, None]:
                yield _sse_event({"type": "answer_start"})
                yield _sse_event({"type": "answer_chunk", "content": final_answer})
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

    return router
