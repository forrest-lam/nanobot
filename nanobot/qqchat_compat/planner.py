"""Skill-driven planner for QQ chat search workflow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.qqchat_compat.schemas import ToolCall


_SKILL_TOOL_PATTERN = re.compile(r"\b(search_chats|search_messages|get_recent_messages|get_recent_chats|get_profiles)\b")
_WORD_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_]{2,20}")
_METADATA_PATTERN = re.compile(r"""metadata:\s*['"]({.*?})['"]""", re.MULTILINE)


@dataclass
class PlannedStep:
    tool: str
    arguments: dict[str, Any]
    reason: str
    source_skill: str
    priority: int


class SkillDrivenPlanner:
    """Generate tool calls based on skill docs and query text.

    Only used under the HTTP (qqchat-compat) channel — other channels
    use the standard AgentLoop skill/tool pipeline without restriction.
    """

    _SKILL_NAMES = (
        "qqchat-search-contacts",
        "qqchat-search-groups",
        "qqchat-search-messages",
    )

    def __init__(self, skill_roots: list[Path], tool_registry=None, prompt_store=None):
        self.skill_roots = skill_roots
        self.tool_registry = tool_registry  # Optional: ToolRegistry for enabling MCP tools on-demand
        self.prompt_store = prompt_store  # Optional: UserPromptStore for per-user prompts
        self.skill_tool_map = self._load_skill_tools()
        self.skill_metadata_map = self._load_skill_metadata()

    def _resolve_skill_path(self, skill_name: str) -> Path | None:
        for root in self.skill_roots:
            path = root / skill_name / "SKILL.md"
            if path.exists():
                return path
        return None

    def _load_skill_tools(self) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for skill in self._SKILL_NAMES:
            path = self._resolve_skill_path(skill)
            text = path.read_text(encoding="utf-8") if path else ""
            tools = list(dict.fromkeys(_SKILL_TOOL_PATTERN.findall(text)))
            mapping[skill] = tools
        return mapping

    def _load_skill_metadata(self) -> dict[str, dict]:
        """Load skill metadata (compatTools) from SKILL.md files."""
        mapping: dict[str, dict] = {}
        for skill in self._SKILL_NAMES:
            path = self._resolve_skill_path(skill)
            if not path:
                continue

            try:
                text = path.read_text(encoding="utf-8")
                # Extract metadata JSON from frontmatter
                match = _METADATA_PATTERN.search(text)
                if match:
                    metadata_json = match.group(1)
                    metadata = json.loads(metadata_json)
                    mapping[skill] = metadata
                    logger.debug("Loaded metadata for skill '{}': {}", skill, metadata)
                else:
                    mapping[skill] = {}
            except Exception as e:
                logger.warning("Failed to parse metadata for skill '{}': {}", skill, e)
                mapping[skill] = {}

        return mapping

    def _extract_keywords(self, query: str) -> list[str]:
        words = _WORD_PATTERN.findall(query)
        seen: set[str] = set()
        out: list[str] = []
        for w in words:
            token = w.strip()
            if len(token) < 2 or token in seen:
                continue
            seen.add(token)
            out.append(token)
            if len(out) >= 5:
                break
        return out or [query.strip()[:20] or "最近"]

    def _contains_any(self, query: str, words: tuple[str, ...]) -> bool:
        return any(w in query for w in words)

    def plan_initial(self, query: str) -> list[PlannedStep]:
        q = query.strip()
        keywords = self._extract_keywords(q)
        kw_display = "、".join(keywords[:3])
        steps: list[PlannedStep] = []

        # Track which skills are activated
        activated_skills: set[str] = set()

        if self._contains_any(q, ("谁", "联系人", "好友", "QQ号", "资料", "在吗")):
            if "search_chats" in self.skill_tool_map.get("qqchat-search-contacts", []):
                steps.append(
                    PlannedStep(
                        tool="search_chats",
                        arguments={"keywords": keywords},
                        reason=f"正在查找「{kw_display}」相关的联系人…",
                        source_skill="qqchat-search-contacts",
                        priority=3,
                    )
                )
                activated_skills.add("qqchat-search-contacts")

        if self._contains_any(q, ("群", "群聊", "群组", "项目群", "技术群")):
            if "search_chats" in self.skill_tool_map.get("qqchat-search-groups", []):
                steps.append(
                    PlannedStep(
                        tool="search_chats",
                        arguments={"keywords": keywords},
                        reason=f"正在查找「{kw_display}」相关的群聊…",
                        source_skill="qqchat-search-groups",
                        priority=3,
                    )
                )
                activated_skills.add("qqchat-search-groups")

        if "search_messages" in self.skill_tool_map.get("qqchat-search-messages", []):
            steps.append(
                PlannedStep(
                    tool="search_messages",
                    arguments={"keywords": keywords},
                    reason=f"正在搜索包含「{kw_display}」的聊天记录…",
                    source_skill="qqchat-search-messages",
                    priority=3,
                )
            )
            activated_skills.add("qqchat-search-messages")

        if not steps:
            steps.append(
                PlannedStep(
                    tool="search_messages",
                    arguments={"keywords": keywords},
                    reason=f"正在搜索包含「{kw_display}」的聊天记录…",
                    source_skill="qqchat-search-messages",
                    priority=1,
                )
            )
            activated_skills.add("qqchat-search-messages")

        # Enable tools from activated skills
        self._activate_skills(activated_skills)

        return steps

    def _activate_skills(self, skill_names: set[str]) -> None:
        """Activate skills by enabling their compatTools.
        
        Args:
            skill_names: Set of skill names to activate
        """
        if not self.tool_registry:
            logger.debug("No tool_registry configured, skipping skill activation")
            return

        for skill_name in skill_names:
            metadata = self.skill_metadata_map.get(skill_name, {})
            compat_tools = metadata.get("nanobot", {}).get("compatTools", [])
            
            if compat_tools:
                enabled_count = 0
                for tool_name in compat_tools:
                    # For MCP tools, they should already be registered (just disabled)
                    # Enable them directly through the registry
                    if self.tool_registry.enable(tool_name) > 0:
                        enabled_count += 1
                
                logger.info(
                    "Activated skill '{}': enabled {}/{} tools",
                    skill_name, enabled_count, len(compat_tools)
                )

    def plan_follow_up(self, round_count: int, query: str) -> list[PlannedStep]:
        keywords = self._extract_keywords(query)
        kw_display = "、".join(keywords[:3])
        if round_count >= 2:
            return []

        return [
            PlannedStep(
                tool="get_recent_chats",
                arguments={"limit": 50},
                reason="首轮结果不够充分，正在获取最近的会话列表以补充上下文…",
                source_skill="qqchat-search-messages",
                priority=2,
            ),
            PlannedStep(
                tool="search_messages",
                arguments={"keywords": keywords},
                reason=f"正在重新搜索「{kw_display}」以获取更多相关记录…",
                source_skill="qqchat-search-messages",
                priority=2,
            ),
        ]

    @staticmethod
    def build_progress_hint(calls: list[ToolCall], is_follow_up: bool = False) -> str:
        """Generate a user-facing progress hint summarising what is being searched."""
        if not calls:
            return ""
        if is_follow_up:
            return f"首轮搜索结果不够充分，正在进行补充检索（共 {len(calls)} 项）…"
        if len(calls) == 1:
            return calls[0].reason
        return f"正在同时执行 {len(calls)} 项检索，请稍候…"

    def build_calls(self, planned_steps: list[PlannedStep], round_count: int) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for idx, step in enumerate(planned_steps, start=1):
            calls.append(
                ToolCall(
                    id=f"r{round_count + 1}_call_{idx}",
                    tool=step.tool,
                    arguments=step.arguments,
                    reason=step.reason,
                    source_skill=step.source_skill,
                    priority=step.priority,
                )
            )
        return calls

    def suggest_followup(
        self,
        query: str,
        memory_records: list[dict[str, Any]],
    ) -> str:
        """Return a single follow-up suggestion based on user memory and current context.

        Priority: memory-based (continue a prior topic) > query-based (drill down).
        The suggestion is appended to final_answer so it persists in conversation
        history, enabling the user to confirm with a short reply like "好的" / "需要".
        """
        keywords = self._extract_keywords(query)
        kw_first = keywords[0] if keywords else query[:10]

        # Prefer memory-based: revisit a recent *different* topic
        if memory_records:
            for rec in reversed(memory_records):
                prev_q = rec.get("query", "").strip()
                if prev_q and prev_q != query.strip():
                    prev_kws = self._extract_keywords(prev_q)
                    prev_display = prev_kws[0] if prev_kws else prev_q[:10]
                    return f"继续上次的话题，帮你看看「{prev_display}」有什么新消息？"

        # Fallback: derive from current query
        if self._contains_any(query, ("说了什么", "聊了什么", "发了什么", "消息", "聊天")):
            return f"需要我帮你总结一下{kw_first}最近讨论的话题吗？"
        if self._contains_any(query, ("谁", "联系人", "好友")):
            return f"要不要看看你和{kw_first}最近聊了些什么？"
        if self._contains_any(query, ("群", "群聊")):
            return f"需要我帮你看看{kw_first}群里最近在聊什么吗？"
        return f"需要我帮你进一步总结「{kw_first}」相关的聊天要点吗？"

    def summarize_results(
        self,
        query: str,
        search_results: list[dict[str, Any]],
        user_uin: str | None = None,
    ) -> str:
        """Summarize search results with optional user-specific personality.
        
        Args:
            query: User's search query
            search_results: List of search results from tools
            user_uin: Optional user UIN for personalized responses
            
        Returns:
            Summary text
        """
        if not search_results:
            return "暂未检索到有效结果。"

        snippets: list[str] = []
        for item in search_results[:20]:
            tool = str(item.get("tool") or item.get("name") or "")
            data = item.get("result")
            text = ""
            if isinstance(data, str):
                text = data
            elif isinstance(data, dict):
                text = str(data.get("message") or data.get("content") or data)
            else:
                text = str(data)
            text = text.strip().replace("\n", " ")
            if text:
                snippets.append(f"[{tool}] {text[:140]}")
            if len(snippets) >= 8:
                break

        if not snippets:
            return "检索已完成，但结果为空或无法解析。"

        # Base summary
        joined = "\n".join(f"- {s}" for s in snippets)
        base_summary = f'围绕"{query}"已检索到以下关键信息：\n{joined}'
        
        # Append user personality hint if available
        if user_uin and self.prompt_store:
            soul = self.prompt_store.get_prompt(user_uin, "SOUL.md")
            if "用户自定义偏好" in soul:
                # Extract custom section for hint
                lines = soul.split("## 用户自定义偏好")
                if len(lines) > 1:
                    custom_section = lines[1].strip()[:200]
                    base_summary += f"\n\n💡 提示：根据你的偏好（{custom_section[:50]}...），以上信息已按你的习惯整理。"
        
        return base_summary
