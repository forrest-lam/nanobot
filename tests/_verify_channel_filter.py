"""Temporary verification script for channel-based skill filtering."""
from pathlib import Path
from nanobot.agent.skills import SkillsLoader

ws = Path(".")

# No channel (default AgentLoop) — should exclude qqchat-search-* skills
loader_default = SkillsLoader(ws)
names_default = [s["name"] for s in loader_default.list_skills(filter_unavailable=False)]
qqchat_in_default = [n for n in names_default if n.startswith("qqchat-search")]
print(f"Default (no channel): {len(names_default)} skills, qqchat-search: {qqchat_in_default}")
assert qqchat_in_default == [], f"Expected no qqchat skills, got {qqchat_in_default}"

# With qqchat_http channel — should include qqchat-search-* skills
loader_http = SkillsLoader(ws, channel="qqchat_http")
names_http = [s["name"] for s in loader_http.list_skills(filter_unavailable=False)]
qqchat_in_http = [n for n in names_http if n.startswith("qqchat-search")]
print(f"qqchat_http channel: {len(names_http)} skills, qqchat-search: {qqchat_in_http}")
assert len(qqchat_in_http) == 3, f"Expected 3 qqchat skills, got {qqchat_in_http}"

# With some other channel — should exclude qqchat-search-*
loader_cli = SkillsLoader(ws, channel="cli")
names_cli = [s["name"] for s in loader_cli.list_skills(filter_unavailable=False)]
qqchat_in_cli = [n for n in names_cli if n.startswith("qqchat-search")]
print(f"cli channel: {len(names_cli)} skills, qqchat-search: {qqchat_in_cli}")
assert qqchat_in_cli == [], f"Expected no qqchat skills, got {qqchat_in_cli}"

print("\nAll assertions passed!")
