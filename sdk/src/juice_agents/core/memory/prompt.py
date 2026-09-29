"""Prompt context helpers for workspace memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import MemoryConfig
from .store import MemoryStore


def build_memory_prompt_context(
    *,
    workspace_dir: str | Path,
    config: MemoryConfig,
) -> dict[str, Any]:
    """Build the dynamic prompt fields consumed by default agent prompts."""

    if not config.enabled:
        return {"memory_enabled": False, "memory_dir": "", "memory_index": ""}
    store = MemoryStore.for_workspace(workspace_dir)
    try:
        memory_index = store.read()
    except Exception:
        memory_index = ""
    return {
        "memory_enabled": True,
        "memory_dir": str(store.root),
        "memory_index": memory_index.strip(),
    }


__all__ = ["build_memory_prompt_context"]
