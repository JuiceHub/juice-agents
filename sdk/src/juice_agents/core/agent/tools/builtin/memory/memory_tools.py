"""Workspace memory tools with `.juice/memory` path isolation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from juice_agents.core.memory.store import MemoryStore

from ...runtime.base_tools import CONTENT_OBSERVATION_CHARS, LIST_OBSERVATION_CHARS, Tool


class _BaseMemoryTool(Tool):
    """Shared workspace resolution for memory tools."""

    def __init__(
        self,
        *,
        workspace_dir: str | Path | None = None,
        enabled: bool = True,
        dream_enabled: bool = True,
    ) -> None:
        super().__init__()
        self.workspace_dir = None if workspace_dir is None else Path(workspace_dir).expanduser().resolve()
        self.enabled = bool(enabled)
        self.dream_enabled = bool(dream_enabled)
        self.owner_agent: Any | None = None

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _workspace_dir(self) -> Path:
        if self.workspace_dir is not None:
            return self.workspace_dir
        context = getattr(self.owner_agent, "runner_context", None)
        runner = getattr(context, "runner", None)
        base_dir = getattr(runner, "base_dir", None)
        if base_dir is not None:
            return Path(base_dir).expanduser().resolve()
        default_base = getattr(self.owner_agent, "_runtime_base_dir", None)
        return Path(default_base or Path.cwd()).expanduser().resolve()

    def _store(self) -> MemoryStore:
        return MemoryStore.for_workspace(self._workspace_dir())


class MemoryReadTool(_BaseMemoryTool):
    _execution_mode = "parallel_safe"
    max_observation_chars = CONTENT_OBSERVATION_CHARS
    name = "memory_read"
    description = "Read a workspace memory file under .juice/memory"
    inputs = {
        "path": {
            "type": "string",
            "description": "Memory-relative path, defaults to MEMORY.md",
            "required": False,
        }
    }
    outputs = {
        "path": {"type": "string", "description": "Resolved memory-relative path"},
        "content": {"type": "string", "description": "File content"},
    }

    def forward(self, path: str | None = None) -> dict[str, Any]:
        store = self._store()
        resolved = store._resolve(path)  # central containment check
        return {"path": store._relative(resolved), "content": store.read(path)}


class MemorySearchTool(_BaseMemoryTool):
    _execution_mode = "parallel_safe"
    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "memory_search"
    description = "Search workspace memory files under .juice/memory"
    inputs = {
        "query": {"type": "string", "description": "Case-insensitive search query"},
        "limit": {"type": "integer", "description": "Maximum hits", "required": False},
    }
    outputs = {"hits": {"type": "array", "description": "Matching memory lines"}}

    def forward(self, query: str, limit: int = 20) -> dict[str, Any]:
        return {"hits": self._store().search(query, limit=int(limit or 20))}


class MemoryWriteTool(_BaseMemoryTool):
    _execution_mode = "serial"
    name = "memory_write"
    description = "Write or update a topic memory file under .juice/memory/topics"
    inputs = {
        "title": {"type": "string", "description": "Topic title"},
        "content": {"type": "string", "description": "Markdown bullet facts"},
        "memory_type": {"type": "string", "description": "Memory category", "required": False},
        "source": {"type": "string", "description": "Memory source label", "required": False},
    }
    outputs = {
        "updated": {"type": "boolean", "description": "Whether the topic was written"},
        "path": {"type": "string", "description": "Written topic path"},
        "title": {"type": "string", "description": "Display title"},
    }

    def forward(
        self,
        title: str,
        content: str,
        memory_type: str = "general",
        source: str = "explicit",
    ) -> dict[str, Any]:
        return self._store().write_topic(
            title=title,
            content=content,
            memory_type=memory_type,
            source=source,
        )


class MemoryForgetTool(_BaseMemoryTool):
    _execution_mode = "serial"
    name = "memory_forget"
    description = "Remove a topic memory and its MEMORY.md index pointer"
    inputs = {
        "path_or_title": {"type": "string", "description": "Topic title or memory-relative path"},
        "reason": {"type": "string", "description": "Forget reason", "required": False},
    }
    outputs = {
        "removed": {"type": "boolean", "description": "Whether a file was removed"},
        "path": {"type": "string", "description": "Removed topic path"},
        "reason": {"type": "string", "description": "Forget reason"},
    }

    def forward(self, path_or_title: str, reason: str = "") -> dict[str, Any]:
        return self._store().forget(path_or_title, reason=reason)


class MemoryStatusTool(_BaseMemoryTool):
    _execution_mode = "parallel_safe"
    name = "memory_status"
    description = "Return workspace memory status and configuration flags"
    inputs = {}
    outputs = {
        "enabled": {"type": "boolean", "description": "Whether memory is enabled"},
        "dream_enabled": {"type": "boolean", "description": "Whether dream is enabled"},
        "memory_dir": {"type": "string", "description": "Workspace memory directory"},
        "topic_count": {"type": "integer", "description": "Number of topic files"},
    }

    def forward(self) -> dict[str, Any]:
        return self._store().status(enabled=self.enabled, dream_enabled=self.dream_enabled)


MEMORY_TOOLS = [
    MemoryReadTool,
    MemorySearchTool,
    MemoryWriteTool,
    MemoryForgetTool,
    MemoryStatusTool,
]


__all__ = [
    "MEMORY_TOOLS",
    "MemoryForgetTool",
    "MemoryReadTool",
    "MemorySearchTool",
    "MemoryStatusTool",
    "MemoryWriteTool",
]
