"""Account-scoped memory persistence for the ``qqchat_http`` channel.

Memory files are stored under ``workspace/qqchat_compat/memory/`` — a
directory exclusive to this channel.  This prevents any cross-channel
memory leakage with the main nanobot ``memory/`` store.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from nanobot.utils.helpers import ensure_dir, safe_filename


class AccountMemoryStore:
    """Simple account-isolated memory records."""

    def __init__(self, workspace: Path):
        self.base_dir = ensure_dir(workspace / "qqchat_compat" / "memory")

    def _user_path(self, user_uin: str) -> Path:
        return self.base_dir / f"{safe_filename(user_uin)}.json"

    def _read_json(self, path: Path) -> dict:
        if not path.exists():
            return {"user_uin": "", "updated_at": "", "items": []}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, TypeError):
            return {"user_uin": "", "updated_at": "", "items": []}

    def append_record(self, user_uin: str, query: str, answer: str, round_count: int) -> None:
        path = self._user_path(user_uin)
        data = self._read_json(path)
        data["user_uin"] = user_uin
        data["updated_at"] = datetime.now(UTC).isoformat()
        items = data.setdefault("items", [])
        items.append(
            {
                "time": datetime.now(UTC).isoformat(),
                "query": query,
                "answer": answer,
                "round_count": round_count,
            }
        )
        if len(items) > 300:
            data["items"] = items[-300:]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_records(self, user_uin: str) -> list[dict]:
        data = self._read_json(self._user_path(user_uin))
        if data.get("user_uin") != user_uin:
            return []
        return list(data.get("items", []))
