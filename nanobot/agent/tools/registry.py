"""Tool registry for dynamic tool management."""

from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    Supports enabling/disabling tools for context-aware injection.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._enabled_tools: set[str] = set()  # Currently enabled tools
        self._always_enabled: set[str] = set()  # Tools that are always enabled (default tools)

    def register(self, tool: Tool, enabled: bool = True, always: bool = False) -> None:
        """Register a tool.
        
        Args:
            tool: Tool to register
            enabled: Whether to enable the tool immediately
            always: Whether this tool should always be enabled (e.g. default tools)
        """
        self._tools[tool.name] = tool
        if always:
            self._always_enabled.add(tool.name)
            self._enabled_tools.add(tool.name)
        elif enabled:
            self._enabled_tools.add(tool.name)

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
        self._enabled_tools.discard(name)
        self._always_enabled.discard(name)

    def enable(self, *tool_names: str) -> int:
        """Enable specified tools.
        
        Args:
            tool_names: Names of tools to enable
            
        Returns:
            Number of tools successfully enabled
        """
        enabled_count = 0
        for name in tool_names:
            if name in self._tools:
                if name not in self._enabled_tools:
                    self._enabled_tools.add(name)
                    logger.debug("Enabled tool: {}", name)
                enabled_count += 1
            else:
                logger.debug("Tool '{}' not found in registry, cannot enable", name)
        return enabled_count

    def disable(self, *tool_names: str) -> int:
        """Disable specified tools (does not affect always-enabled tools).
        
        Args:
            tool_names: Names of tools to disable
            
        Returns:
            Number of tools successfully disabled
        """
        disabled_count = 0
        for name in tool_names:
            if name in self._always_enabled:
                logger.debug("Tool '{}' is always-enabled, cannot disable", name)
                continue
            if name in self._enabled_tools:
                self._enabled_tools.discard(name)
                logger.debug("Disabled tool: {}", name)
                disabled_count += 1
        return disabled_count

    def reset_enabled_tools(self) -> None:
        """Reset enabled tools to only always-enabled tools."""
        self._enabled_tools = self._always_enabled.copy()
        logger.debug("Reset enabled tools to always-enabled set: {}", self._always_enabled)

    def enable_all(self) -> None:
        """Enable all registered tools."""
        self._enabled_tools = set(self._tools.keys())
        logger.debug("Enabled all {} tools", len(self._enabled_tools))

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def is_enabled(self, name: str) -> bool:
        """Check if a tool is currently enabled."""
        return name in self._enabled_tools

    def get_definitions(self, only_enabled: bool = True) -> list[dict[str, Any]]:
        """Get tool definitions in OpenAI format.
        
        Args:
            only_enabled: If True, only return enabled tools
            
        Returns:
            List of tool definitions
        """
        if only_enabled:
            return [
                tool.to_schema()
                for name, tool in self._tools.items()
                if name in self._enabled_tools
            ]
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """Execute a tool by name with given parameters."""
        _HINT = "\n\n[Analyze the error above and try a different approach.]"

        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        try:
            # Attempt to cast parameters to match schema types
            params = tool.cast_params(params)
            
            # Validate parameters
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _HINT
            result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                return result + _HINT
            return result
        except Exception as e:
            return f"Error executing {name}: {str(e)}" + _HINT

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
