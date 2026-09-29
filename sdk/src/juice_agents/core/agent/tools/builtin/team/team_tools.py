"""Team task, inbox and member tools routed through the normal ToolManager.

The bound Agent instance is only an identity handle. TeamManager owns all
mutable state, so tool calls never maintain a second task board or inbox.
"""

from __future__ import annotations

from typing import Any

from ...runtime.base_tools import LIST_OBSERVATION_CHARS, Tool


class TeamTool(Tool):
    """Resolve the caller from AgentManager, never from model supplied args."""

    _execution_mode = "serial"

    def __init__(self) -> None:
        super().__init__()
        self.owner_agent: Any | None = None

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _runtime(self) -> tuple[Any, Any, Any]:
        runner = getattr(getattr(self.owner_agent, "runner_context", None), "runner", None)
        if runner is None or str(getattr(runner, "mode_id", "")) != "team":
            raise PermissionError("Team tools require a Team Runner")
        manager = getattr(runner, "team_manager", None)
        if manager is None:
            raise RuntimeError("TeamManager is not initialized")
        managed = next(
            (item for item in runner.agent_manager.live_agents.values() if item.instance is self.owner_agent),
            None,
        )
        if managed is None:
            raise PermissionError("Tool owner is not an active AgentManager member")
        if not managed.is_root:
            metadata = dict(getattr(managed, "metadata", {}) or {})
            if (getattr(managed, "role", None) != "teammate"
                    or metadata.get("team_member") != managed.agent_name
                    or metadata.get("team_run_id") != runner.state.get("team_run_id")):
                raise PermissionError("Tool owner is not a member of the current Team run")
        return runner, manager, managed

    def _root(self) -> tuple[Any, Any, Any]:
        runner, manager, managed = self._runtime()
        if not managed.is_root:
            raise PermissionError("Only Team root can manage members or finish Team")
        return runner, manager, managed


class TeamTaskCreateTool(TeamTool):
    name = "team_task_create"
    description = "Create a durable Team task for existing eligible members. Root only."
    inputs = {
        "title": {"type": "string", "description": "Short task title"},
        "description": {"type": "string", "description": "Task details", "required": False},
        "eligible_members": {"type": "any", "description": "'all' or a nonempty list of existing member names"},
        "dependencies": {"type": "list", "description": "Task IDs that must complete first", "required": False},
    }
    outputs = {"task": {"type": "object", "description": "Created task"}}

    def forward(self, title: str, eligible_members: str | list[str], description: str = "", dependencies: list[str] | None = None) -> dict[str, Any]:
        _, manager, _ = self._root()
        return {"task": manager.add_task(title, description, eligible_members, dependencies or ())}


class TeamTaskListTool(TeamTool):
    name = "team_task_list"
    is_read_only = True
    _execution_mode = "parallel_safe"
    max_observation_chars = LIST_OBSERVATION_CHARS
    description = "List durable Team tasks, eligibility, claims, dependencies and status."
    inputs: dict[str, dict[str, Any]] = {}
    outputs = {"tasks": {"type": "list", "description": "Current Team tasks"}}

    def forward(self) -> dict[str, Any]:
        _, manager, _ = self._runtime()
        return {"tasks": manager.list_tasks()}


class TeamTaskClaimTool(TeamTool):
    name = "team_task_claim"
    description = "Atomically claim a ready Team task. Omit task_id for the oldest ready task."
    inputs = {"task_id": {"type": "string", "description": "Optional task ID", "required": False}}
    outputs = {"task": {"type": "object", "description": "Claimed task, or null if none is ready"}}

    def forward(self, task_id: str = "") -> dict[str, Any]:
        _, manager, managed = self._runtime()
        if managed.is_root:
            raise PermissionError("Team root cannot execute tasks")
        task = manager.claim_task(task_id, managed.agent_name) if task_id else manager.claim_next(managed.agent_name)
        return {"task": task}


class TeamTaskCompleteTool(TeamTool):
    name = "team_task_complete"
    description = "Complete a task claimed by the current Team member."
    inputs = {
        "task_id": {"type": "string", "description": "Claimed task ID"},
        "result": {"type": "string", "description": "Concise result and evidence", "required": False},
    }
    outputs = {"task": {"type": "object", "description": "Completed task"}}

    def forward(self, task_id: str, result: str = "") -> dict[str, Any]:
        _, manager, managed = self._runtime()
        if managed.is_root:
            raise PermissionError("Team root cannot execute tasks")
        return {"task": manager.complete_task(task_id, managed.agent_name, result)}


class TeamTaskUpdateTool(TeamTool):
    name = "team_task_update"
    description = "Edit, reassign, complete or delete a Team task. Root only."
    inputs = {
        "task_id": {"type": "string", "description": "Task ID"},
        "title": {"type": "string", "description": "New title", "required": False},
        "description": {"type": "string", "description": "New details", "required": False},
        "eligible_members": {"type": "any", "description": "'all' or member name list", "required": False},
        "dependencies": {"type": "list", "description": "Replacement prerequisite IDs", "required": False},
        "status": {"type": "string", "description": "pending, in_progress or completed", "required": False},
        "result": {"type": "string", "description": "Task result", "required": False},
        "delete": {"type": "boolean", "description": "Delete task", "required": False},
    }
    outputs = {"task": {"type": "object", "description": "Updated or deleted task"}}

    def forward(self, task_id: str, title: str | None = None, description: str | None = None,
                eligible_members: str | list[str] | None = None, dependencies: list[str] | None = None,
                status: str | None = None, result: str | None = None, delete: bool = False) -> dict[str, Any]:
        _, manager, _ = self._root()
        return {"task": manager.update_task(task_id, title=title, description=description,
                                            eligible_members=eligible_members, dependencies=dependencies,
                                            status=status, result=result, delete=delete)}


class TeamCreateTool(TeamTool):
    name = "team_create"
    description = "Create an empty Team definition and start its new task board. Root only."
    inputs = {
        "name": {"type": "string", "description": "Unique Team name"},
        "description": {"type": "string", "description": "Team purpose", "required": False},
    }
    outputs = {"team": {"type": "object", "description": "New Team snapshot"}}

    def forward(self, name: str, description: str = "") -> dict[str, Any]:
        runner, _, _ = self._root()
        return {"team": runner.create_team(name, description=description)}


class TeamUseTool(TeamTool):
    name = "team_use"
    description = "Start a new task board from an existing Team definition. Root only."
    inputs = {"name": {"type": "string", "description": "Existing Team name"}}
    outputs = {"team": {"type": "object", "description": "Selected Team snapshot"}}

    def forward(self, name: str) -> dict[str, Any]:
        runner, _, _ = self._root()
        return {"team": runner.use_team(name)}


class SendMessageTool(TeamTool):
    name = "send_message"
    description = "Queue a durable private Team message for a member or root."
    inputs = {
        "recipient": {"type": "string", "description": "Recipient Team member name, or root"},
        "text": {"type": "string", "description": "Message content"},
    }
    outputs = {"message": {"type": "object", "description": "Queued message with ID"}}

    def forward(self, recipient: str, text: str) -> dict[str, Any]:
        _, manager, managed = self._runtime()
        sender = "root" if managed.is_root else managed.agent_name
        return {"message": manager.send_message(sender, recipient, text)}


class TeamMemberStopRequestTool(TeamTool):
    name = "team_member_stop_request"
    description = "Ask root to close your member session; this does not stop it automatically."
    inputs = {"reason": {"type": "string", "description": "Why this member should stop"}}
    outputs = {"message": {"type": "object", "description": "Structured request queued for root"}}

    def forward(self, reason: str) -> dict[str, Any]:
        _, manager, managed = self._runtime()
        if managed.is_root:
            raise PermissionError("Only a Team member can request its own stop")
        return {"message": manager.request_member_stop(managed.agent_name, reason)}


class TeamMemberCreateTool(TeamTool):
    name = "team_member_create"
    description = (
        "Create a member from exactly one shared agent_name or Team-local config. "
        "For a local role, config can be {'description': '...', 'instructions': '...'}; "
        "Team supplies persistent lifecycle and team mode. Root only."
    )
    inputs = {
        "name": {"type": "string", "description": "Unique Team member name"},
        "agent_name": {"type": "string", "description": "Shared Agent name", "required": False},
        "config": {"type": "object", "description": "Team-local Agent configuration", "required": False},
    }
    outputs = {"member": {"type": "object", "description": "Created Team member"}}

    def forward(self, name: str, agent_name: str | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
        runner, _, _ = self._root()
        return {"member": runner.create_team_member(name, agent_name=agent_name, config=config)}


class TeamMemberRestartTool(TeamTool):
    name = "team_member_restart"
    description = "Start a fresh session for a Team member while keeping queued messages. Root only."
    inputs = {"name": {"type": "string", "description": "Team member name"}}
    outputs = {"member": {"type": "object", "description": "Restarted Team member"}}

    def forward(self, name: str) -> dict[str, Any]:
        runner, _, _ = self._root()
        return {"member": runner.restart_team_member(name)}


class TeamMemberCloseTool(TeamTool):
    name = "team_member_close"
    description = "Close an idle Team member and return unfinished work to the board. Root only."
    inputs = {"name": {"type": "string", "description": "Team member name"}}
    outputs = {"member": {"type": "object", "description": "Closed Team member"}}

    def forward(self, name: str) -> dict[str, Any]:
        runner, _, _ = self._root()
        return {"member": runner.close_team_member(name)}


class TeamFinishTool(TeamTool):
    name = "team_finish"
    description = "Finish Team after all tasks, inboxes and member calls are settled. Root only."
    inputs: dict[str, dict[str, Any]] = {}
    outputs = {"team": {"type": "object", "description": "Finished Team snapshot"}}

    def forward(self) -> dict[str, Any]:
        runner, _, _ = self._root()
        return {"team": runner.finish_team()}


__all__ = [
    "TeamTaskCreateTool", "TeamTaskListTool", "TeamTaskClaimTool", "TeamTaskCompleteTool", "TeamTaskUpdateTool",
    "TeamCreateTool", "TeamUseTool",
    "SendMessageTool", "TeamMemberStopRequestTool", "TeamMemberCreateTool", "TeamMemberRestartTool",
    "TeamMemberCloseTool", "TeamFinishTool",
]
