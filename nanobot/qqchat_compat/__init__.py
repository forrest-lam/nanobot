"""QQChatAgentServer HTTP 协议兼容层。

This module implements a separate HTTP channel with its own skill/tool
isolation policy.  Restrictions defined here (tool whitelist, skill
scoping, memory isolation) apply **only** to requests entering through
this HTTP channel.  Other nanobot channels (cli, telegram, qq, etc.)
continue to use the full AgentLoop pipeline without any restriction.
"""

from nanobot.qqchat_compat._channel import CHANNEL  # noqa: E402
from nanobot.qqchat_compat.server import create_app  # noqa: E402

__all__ = ["CHANNEL", "create_app"]
