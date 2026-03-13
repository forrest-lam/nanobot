"""Skill-driven planner for QQ chat search workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.qqchat_compat.schemas import ToolCall


_SKILL_TOOL_PATTERN = re.compile(r"\b(search_chats|search_messages|get_recent_messages|get_recent_chats|get_profiles)\b")
_WORD_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_]{2,20}")


@dataclass(slots=True)
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

    def __init__(self, skill_roots: list[Path]):
        self.skill_roots = skill_roots
        self.skill_tool_map = self._load_skill_tools()

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
        steps: list[PlannedStep] = []

        if self._contains_any(q, ("谁", "联系人", "好友", "QQ号", "资料", "在吗")):
            if "search_chats" in self.skill_tool_map.get("qqchat-search-contacts", []):
                steps.append(
                    PlannedStep(
                        tool="search_chats",
                        arguments={"keywords": keywords},
                        reason="先定位目标联系人或相关会话",
                        source_skill="qqchat-search-contacts",
                        priority=3,
                    )
                )

        if self._contains_any(q, ("群", "群聊", "群组", "项目群", "技术群")):
            if "search_chats" in self.skill_tool_map.get("qqchat-search-groups", []):
                steps.append(
                    PlannedStep(
                        tool="search_chats",
                        arguments={"keywords": keywords},
                        reason="先定位目标群聊",
                        source_skill="qqchat-search-groups",
                        priority=3,
                    )
                )

        if "search_messages" in self.skill_tool_map.get("qqchat-search-messages", []):
            steps.append(
                PlannedStep(
                    tool="search_messages",
                    arguments={"keywords": keywords},
                    reason="按关键词检索聊天记录并携带上下文",
                    source_skill="qqchat-search-messages",
                    priority=3,
                )
            )

        if not steps:
            steps.append(
                PlannedStep(
                    tool="search_messages",
                    arguments={"keywords": keywords},
                    reason="默认消息检索",
                    source_skill="qqchat-search-messages",
                    priority=1,
                )
            )
        return steps

    def plan_follow_up(self, round_count: int, query: str) -> list[PlannedStep]:
        keywords = self._extract_keywords(query)
        if round_count >= 2:
            return []

        return [
            PlannedStep(
                tool="get_recent_chats",
                arguments={"limit": 50},
                reason="搜索结果不足，拉取最近会话缩小范围",
                source_skill="qqchat-search-messages",
                priority=2,
            ),
            PlannedStep(
                tool="search_messages",
                arguments={"keywords": keywords},
                reason="在补充会话信息后再次检索关键词",
                source_skill="qqchat-search-messages",
                priority=2,
            ),
        ]

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

    def summarize_results(self, query: str, search_results: list[dict[str, Any]]) -> str:
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

        joined = "\n".join(f"- {s}" for s in snippets)
        return f"围绕“{query}”已检索到以下关键信息：\n{joined}"
