"""Public plugin registry API."""

from .registry import PluginRegistry
from .types import (
    PLUGIN_MANIFEST_RELATIVE_PATH,
    PluginConflict,
    PluginConflictError,
    PluginManifest,
    PluginMetadata,
    PluginRecord,
)

__all__ = [
    "PLUGIN_MANIFEST_RELATIVE_PATH",
    "PluginConflict",
    "PluginConflictError",
    "PluginManifest",
    "PluginMetadata",
    "PluginRecord",
    "PluginRegistry",
]
