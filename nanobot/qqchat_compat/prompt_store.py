"""User-scoped prompt templates for the ``qqchat_http`` channel.

Each user gets their own SOUL.md, TOOLS.md, and USER.md files that can be
customized independently. On first access, templates are copied from the
default templates/ directory as a baseline.
"""

from __future__ import annotations

from pathlib import Path

from nanobot.utils.helpers import ensure_dir, safe_filename


class UserPromptStore:
    """Manages per-user prompt templates (SOUL, TOOLS, USER)."""

    TEMPLATE_FILES = ["SOUL.md", "TOOLS.md", "USER.md"]

    def __init__(self, workspace: Path, templates_dir: Path):
        """Initialize user prompt store.
        
        Args:
            workspace: Workspace root directory
            templates_dir: Directory containing default template files
        """
        self.base_dir = ensure_dir(workspace / "qqchat_compat" / "prompts")
        self.templates_dir = templates_dir

    def _user_dir(self, user_uin: str) -> Path:
        """Get the prompt directory for a specific user."""
        return ensure_dir(self.base_dir / safe_filename(user_uin))

    def _ensure_user_prompts(self, user_uin: str) -> None:
        """Ensure user has prompt files, creating from templates if needed."""
        user_dir = self._user_dir(user_uin)
        
        for filename in self.TEMPLATE_FILES:
            user_file = user_dir / filename
            if not user_file.exists():
                template_file = self.templates_dir / filename
                if template_file.exists():
                    # Copy template as starting point
                    user_file.write_text(
                        template_file.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )

    def get_prompt(self, user_uin: str, prompt_name: str) -> str:
        """Get a specific prompt file content for a user.
        
        Args:
            user_uin: User's QQ number
            prompt_name: Prompt filename (SOUL.md, TOOLS.md, USER.md)
            
        Returns:
            Prompt content, or empty string if file doesn't exist
        """
        self._ensure_user_prompts(user_uin)
        
        user_file = self._user_dir(user_uin) / prompt_name
        if user_file.exists():
            return user_file.read_text(encoding="utf-8")
        return ""

    def get_all_prompts(self, user_uin: str) -> dict[str, str]:
        """Get all prompt files for a user.
        
        Returns:
            Dictionary mapping filename to content
        """
        self._ensure_user_prompts(user_uin)
        
        result = {}
        for filename in self.TEMPLATE_FILES:
            content = self.get_prompt(user_uin, filename)
            if content:
                result[filename] = content
        return result

    def update_prompt(
        self,
        user_uin: str,
        prompt_name: str,
        content: str,
        append: bool = False,
    ) -> None:
        """Update or append to a user's prompt file.
        
        Args:
            user_uin: User's QQ number
            prompt_name: Prompt filename (SOUL.md, TOOLS.md, USER.md)
            content: Content to write or append
            append: If True, append to existing content; if False, overwrite
        """
        self._ensure_user_prompts(user_uin)
        
        user_file = self._user_dir(user_uin) / prompt_name
        
        if append and user_file.exists():
            existing = user_file.read_text(encoding="utf-8")
            # Add separator if file doesn't end with newline
            separator = "\n\n" if not existing.endswith("\n") else "\n"
            content = existing + separator + content
        
        user_file.write_text(content, encoding="utf-8")

    def append_personality(self, user_uin: str, trait: str) -> None:
        """Append a personality trait or instruction to user's SOUL.md.
        
        This is a convenience method for gradually building user personality.
        
        Args:
            user_uin: User's QQ number
            trait: Personality trait or instruction to add
        """
        self._ensure_user_prompts(user_uin)
        
        # Append to SOUL.md under a custom section
        user_file = self._user_dir(user_uin) / "SOUL.md"
        existing = user_file.read_text(encoding="utf-8")
        
        # Find or create "## 用户自定义偏好" section
        custom_section = "## 用户自定义偏好"
        if custom_section not in existing:
            # Add custom section at the end
            separator = "\n\n" if not existing.endswith("\n") else "\n"
            new_content = f"{existing}{separator}{custom_section}\n\n- {trait}\n"
        else:
            # Append to existing section
            separator = "\n" if existing.endswith("\n") else "\n\n"
            new_content = f"{existing}{separator}- {trait}\n"
        
        user_file.write_text(new_content, encoding="utf-8")

    def reset_prompt(self, user_uin: str, prompt_name: str) -> None:
        """Reset a user's prompt file to default template.
        
        Args:
            user_uin: User's QQ number
            prompt_name: Prompt filename to reset
        """
        user_file = self._user_dir(user_uin) / prompt_name
        template_file = self.templates_dir / prompt_name
        
        if template_file.exists():
            user_file.write_text(
                template_file.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

    def delete_user_prompts(self, user_uin: str) -> None:
        """Delete all prompt files for a user (resets to defaults on next access)."""
        user_dir = self._user_dir(user_uin)
        if user_dir.exists():
            for file in user_dir.glob("*.md"):
                file.unlink()
            # Try to remove directory if empty
            try:
                user_dir.rmdir()
            except OSError:
                pass  # Directory not empty or other error
