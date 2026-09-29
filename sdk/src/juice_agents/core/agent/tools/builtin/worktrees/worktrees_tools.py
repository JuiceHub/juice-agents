"""Agent-facing worktree management tools."""

from __future__ import annotations

from typing import Any

from ...runtime.base_tools import LIST_OBSERVATION_CHARS, Tool


class _RunnerWorktreeTool(Tool):
    """Base helper for tools that need the current Runner context."""

    owner_agent: Any | None = None

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _runner(self) -> Any:
        context = getattr(self.owner_agent, "runner_context", None) if self.owner_agent is not None else None
        runner = getattr(context, "runner", None)
        if runner is None:
            raise RuntimeError(f"{self.name} requires runner context")
        return runner


class EnterWorktreeTool(_RunnerWorktreeTool):
    name = "enter_worktree"
    description = "Create or enter a git worktree and make it the active workspace for subsequent tools."
    inputs = {
        "name": {"type": "string", "description": "Optional worktree name", "required": False},
    }
    outputs = {"worktree": {"type": "object", "description": "Active worktree status"}}

    def forward(self, name: str | None = None) -> dict[str, Any]:
        return dict(self._runner().enter_worktree(name))


class ExitWorktreeTool(_RunnerWorktreeTool):
    name = "exit_worktree"
    description = "Exit the active git worktree and remove it when safe."
    inputs = {
        "discard": {
            "type": "boolean",
            "description": "Force removal even when dirty or ahead of base",
            "required": False,
        },
    }
    outputs = {"worktree": {"type": "object", "description": "Removed worktree status"}}

    def forward(self, discard: bool = False) -> dict[str, Any]:
        return dict(self._runner().exit_worktree(discard=bool(discard)))


class ListWorktreesTool(_RunnerWorktreeTool):
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "list_worktrees"
    description = "List managed git worktrees for the current repository."
    is_read_only = True
    inputs: dict[str, dict[str, Any]] = {}
    outputs = {"worktrees": {"type": "list", "description": "Managed worktree statuses"}}

    def forward(self) -> dict[str, Any]:
        return {"worktrees": list(self._runner().list_worktrees())}


class WorktreeStatusTool(_RunnerWorktreeTool):
    name = "worktree_status"
    description = "Return active or named git worktree status."
    is_read_only = True
    inputs = {
        "name": {"type": "string", "description": "Optional worktree name", "required": False},
    }
    outputs = {"worktree": {"type": "object", "description": "Worktree status"}}

    def forward(self, name: str | None = None) -> dict[str, Any]:
        return dict(self._runner().worktree_status(name))


WORKTREE_TOOLS = [
    EnterWorktreeTool,
    ExitWorktreeTool,
    ListWorktreesTool,
    WorktreeStatusTool,
]

__all__ = [
    "EnterWorktreeTool",
    "ExitWorktreeTool",
    "ListWorktreesTool",
    "WorktreeStatusTool",
    "WORKTREE_TOOLS",
]
