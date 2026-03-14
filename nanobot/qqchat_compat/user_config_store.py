"""User configuration store for client capabilities and preferences.

Stores per-user configuration including:
- Available MCP tools from client
- Client version and metadata
- User identity (UIN, UID, nickname)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass
class UserConfig:
    """Configuration for a single user."""

    user_uin: str
    user_uid: str = ""
    user_nick: str = ""
    available_mcp_tools: list[str] = field(default_factory=list)
    client_version: str = ""
    client_metadata: dict[str, Any] = field(default_factory=dict)
    initialized_at: str = ""
    last_updated: str = ""


class UserConfigStore:
    """Store and retrieve user configurations."""

    def __init__(self, base_dir: Path):
        """Initialize the user config store.
        
        Args:
            base_dir: Base directory for user configs (e.g., ~/.nanobot/users/)
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._cache: dict[str, UserConfig] = {}

    def _config_file(self, user_uin: str) -> Path:
        """Get config file path for a user."""
        user_dir = self.base_dir / user_uin
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir / "config.json"

    def get(self, user_uin: str) -> UserConfig | None:
        """Get user configuration.
        
        Args:
            user_uin: User's QQ number
            
        Returns:
            UserConfig if exists, None otherwise
        """
        with self._lock:
            # Check cache first
            if user_uin in self._cache:
                return self._cache[user_uin]
            
            # Load from disk
            config_file = self._config_file(user_uin)
            if not config_file.exists():
                return None
            
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
                config = UserConfig(**data)
                self._cache[user_uin] = config
                return config
            except Exception:
                return None

    def save(self, config: UserConfig) -> None:
        """Save user configuration.
        
        Args:
            config: UserConfig to save
        """
        with self._lock:
            config_file = self._config_file(config.user_uin)
            config_file.write_text(
                json.dumps(asdict(config), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._cache[config.user_uin] = config

    def update(
        self,
        user_uin: str,
        user_uid: str = "",
        user_nick: str = "",
        available_mcp_tools: list[str] | None = None,
        client_version: str = "",
        client_metadata: dict[str, Any] | None = None,
    ) -> UserConfig:
        """Update or create user configuration.
        
        Args:
            user_uin: User's QQ number
            user_uid: User's QQ UID
            user_nick: User's nickname
            available_mcp_tools: List of available MCP tools
            client_version: Client version string
            client_metadata: Additional client metadata
            
        Returns:
            Updated UserConfig
        """
        from datetime import UTC, datetime
        
        with self._lock:
            config = self.get(user_uin)
            now = datetime.now(UTC).isoformat()
            
            if config is None:
                # Create new config
                config = UserConfig(
                    user_uin=user_uin,
                    user_uid=user_uid,
                    user_nick=user_nick,
                    available_mcp_tools=available_mcp_tools or [],
                    client_version=client_version,
                    client_metadata=client_metadata or {},
                    initialized_at=now,
                    last_updated=now,
                )
            else:
                # Update existing config
                if user_uid:
                    config.user_uid = user_uid
                if user_nick:
                    config.user_nick = user_nick
                if available_mcp_tools is not None:
                    config.available_mcp_tools = available_mcp_tools
                if client_version:
                    config.client_version = client_version
                if client_metadata is not None:
                    config.client_metadata = client_metadata
                config.last_updated = now
            
            self.save(config)
            return config

    def list_all(self) -> list[UserConfig]:
        """List all user configurations.
        
        Returns:
            List of all UserConfig objects
        """
        with self._lock:
            configs = []
            for user_dir in self.base_dir.iterdir():
                if user_dir.is_dir():
                    config = self.get(user_dir.name)
                    if config:
                        configs.append(config)
            return configs

    def delete(self, user_uin: str) -> bool:
        """Delete user configuration.
        
        Args:
            user_uin: User's QQ number
            
        Returns:
            True if deleted, False if not found
        """
        with self._lock:
            config_file = self._config_file(user_uin)
            if config_file.exists():
                config_file.unlink()
                self._cache.pop(user_uin, None)
                return True
            return False
