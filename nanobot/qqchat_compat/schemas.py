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


class InitRequest(BaseModel):
    """Incoming /init request payload for client initialization."""

    user_uin: str
    session_id: str = "default"
    user_uid: str = ""
    user_nick: str = ""
    available_mcp_tools: list[str] = Field(default_factory=list)
    client_version: str = ""
    client_metadata: dict[str, Any] = Field(default_factory=dict)


class QueryRequest(BaseModel):
    """Incoming /query request payload."""

    query: str
    session_id: str = "default"
    user_uin: str
    user_uid: str = ""
    user_nick: str = ""
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


class InitResponse(BaseModel):
    """Response for /init endpoint."""

    status: Literal["success", "error"]
    message: str
    user_uin: str
    user_identity_initialized: bool = False
    available_skills: list[str] = Field(default_factory=list)
    enabled_skills: list[str] = Field(default_factory=list)
    available_mcp_tools: list[str] = Field(default_factory=list, description="Actually available MCP tools on server")
    enabled_mcp_tools: list[str] = Field(default_factory=list, description="MCP tools enabled for this channel")
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
