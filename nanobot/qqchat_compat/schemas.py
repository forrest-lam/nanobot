"""Schema definitions for QQChat compatibility endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """A planned tool call returned to the client."""

    id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    source_skill: str = ""
    priority: int = 1


class QueryRequest(BaseModel):
    """Incoming /query request payload."""

    query: str
    session_id: str = "default"
    user_uin: str
    user_recent_chats: list[dict[str, Any]] = Field(default_factory=list)
    current_time: str = ""
    stream: bool = False


class SearchResultRequest(BaseModel):
    """Incoming /submit_search_results request payload."""

    session_id: str
    user_uin: str
    search_results: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False


class CompatResponse(BaseModel):
    """Compatibility response envelope."""

    status: Literal["need_search", "final_answer", "error"]
    channel: str = "qqchat_http"
    session_id: str
    user_uin: str
    need_search: bool = False
    mcp_calls: list[ToolCall] = Field(default_factory=list)
    progress_hint: str = ""
    final_answer: str = ""
    error: str = ""


class SessionSnapshot(BaseModel):
    """Readable session status for debug endpoint."""

    key: str
    channel: str = "qqchat_http"
    user_uin: str
    session_id: str
    status: str
    round_count: int
    created_at: datetime
    updated_at: datetime
