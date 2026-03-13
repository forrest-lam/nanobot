"""Tool access policy for the ``qqchat_http`` channel **only**.

This policy is applied exclusively within the QQChat compatibility HTTP
channel.  Other nanobot channels (cli, telegram, qq …) are NOT affected;
they use the standard ``AgentLoop`` tool registry without any restriction.
"""

from __future__ import annotations

from dataclasses import dataclass


# Prefixes unconditionally blocked under the qqchat_http channel.
_BLOCKED_PREFIXES = (
    "exec",
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "cron",
)


@dataclass(slots=True)
class ToolPolicy:
    """Default-deny tool policy — scoped to the ``qqchat_http`` channel."""

    allowed_tools: set[str]

    def is_allowed(self, tool_name: str) -> bool:
        normalized = (tool_name or "").strip()
        if not normalized:
            return False
        if normalized.startswith(_BLOCKED_PREFIXES):
            return False
        return normalized in self.allowed_tools

    def filter_allowed(self, tool_names: list[str]) -> list[str]:
        return [name for name in tool_names if self.is_allowed(name)]
