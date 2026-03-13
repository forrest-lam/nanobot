"""FastAPI app builder for the ``qqchat_http`` channel.

This creates a completely independent HTTP service with its own:
- Tool whitelist  (only web-search + QQ chat retrieval)
- Skill scope     (only qqchat-search-* skills)
- Session store   (keyed by ``qqchat_http:{user_uin}:{session_id}``)
- Memory store    (per-account files)

None of these restrictions affect any other nanobot channel.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from nanobot.config.schema import QQChatCompatConfig
from nanobot.qqchat_compat.memory_store import AccountMemoryStore
from nanobot.qqchat_compat.planner import SkillDrivenPlanner
from nanobot.qqchat_compat.routes import create_router
from nanobot.qqchat_compat.session_store import SessionStore
from nanobot.qqchat_compat.tool_policy import ToolPolicy


DEFAULT_ALLOWED_TOOLS = {
    "web_search",
    "web_fetch",
    "search_chats",
    "search_messages",
    "get_recent_messages",
    "get_recent_chats",
    "get_profiles",
}


def create_app(config: QQChatCompatConfig, workspace: Path) -> FastAPI:
    app = FastAPI(title="nanobot QQChat Compat API")

    allowed_tools = set(config.allowed_tools) if config.allowed_tools else set(DEFAULT_ALLOWED_TOOLS)
    policy = ToolPolicy(allowed_tools=allowed_tools)
    session_store = SessionStore(
        ttl_seconds=config.session_ttl_seconds,
        max_sessions=config.max_sessions,
    )
    memory_store = AccountMemoryStore(workspace)
    package_skills_root = Path(__file__).resolve().parent.parent / "skills"
    workspace_skills_root = workspace / "skills"
    planner = SkillDrivenPlanner(skill_roots=[workspace_skills_root, package_skills_root])

    app.include_router(
        create_router(
            session_store=session_store,
            memory_store=memory_store,
            planner=planner,
            policy=policy,
        )
    )
    return app
