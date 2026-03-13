"""In-memory session store with account isolation and TTL cleanup.

Sessions managed here belong exclusively to the ``qqchat_http`` channel.
They are keyed by ``qqchat_http:{user_uin}:{session_id}`` to avoid any
collision with sessions from other nanobot channels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from nanobot.qqchat_compat.schemas import ToolCall

from nanobot.qqchat_compat._channel import CHANNEL as _CHANNEL_PREFIX


@dataclass(slots=True)
class CompatSession:
    user_uin: str
    session_id: str
    query: str = ""
    current_time: str = ""
    status: str = "idle"
    round_count: int = 0
    pending_calls: list[ToolCall] = field(default_factory=list)
    search_results: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def key(self) -> str:
        return f"{_CHANNEL_PREFIX}:{self.user_uin}:{self.session_id}"


class SessionStore:
    """Account-isolated session store."""

    def __init__(self, ttl_seconds: int = 1800, max_sessions: int = 500):
        self.ttl_seconds = max(60, ttl_seconds)
        self.max_sessions = max(10, max_sessions)
        self._lock = RLock()
        self._sessions: dict[str, CompatSession] = {}

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _session_key(self, user_uin: str, session_id: str) -> str:
        return f"{_CHANNEL_PREFIX}:{user_uin}:{session_id}"

    def cleanup_expired(self) -> None:
        with self._lock:
            now = self._now()
            expired_keys = [
                key
                for key, session in self._sessions.items()
                if (now - session.updated_at).total_seconds() > self.ttl_seconds
            ]
            for key in expired_keys:
                self._sessions.pop(key, None)

            if len(self._sessions) > self.max_sessions:
                sorted_items = sorted(self._sessions.items(), key=lambda item: item[1].updated_at)
                overflow = len(self._sessions) - self.max_sessions
                for key, _ in sorted_items[:overflow]:
                    self._sessions.pop(key, None)

    def get_or_create(self, user_uin: str, session_id: str) -> CompatSession:
        self.cleanup_expired()
        with self._lock:
            key = self._session_key(user_uin, session_id)
            session = self._sessions.get(key)
            if session is None:
                session = CompatSession(user_uin=user_uin, session_id=session_id)
                self._sessions[key] = session
            session.updated_at = self._now()
            return session

    def get(self, user_uin: str, session_id: str) -> CompatSession | None:
        self.cleanup_expired()
        with self._lock:
            return self._sessions.get(self._session_key(user_uin, session_id))

    def save(self, session: CompatSession) -> None:
        with self._lock:
            session.updated_at = self._now()
            self._sessions[session.key] = session

    def list_snapshots(self) -> list[CompatSession]:
        self.cleanup_expired()
        with self._lock:
            return sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)
