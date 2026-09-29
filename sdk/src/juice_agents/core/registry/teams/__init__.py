"""Static Team declarations exposed through the common Registry contract.

Team configuration is workspace-level static data, alongside Agent/Tool/Skill
declarations.  Runtime tasks, messages, inboxes and sessions are not handled
here; the Runner-composed Managers own those per-runner records.
"""

from .registry import TeamRegistry
from .store import TeamConfigStore
from .types import TeamConfig, TeamManifest

__all__ = [
    "TeamConfig",
    "TeamManifest",
    "TeamRegistry",
    "TeamConfigStore",
]
