"""Functional checks for the Team ToolManager surface."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from juice_agents.core.agent.root_config import build_root_config
from juice_agents.core.agent.tools.builtin.agents.agents_tools import AgentsListTool, AgentViewTool
from juice_agents.core.agent.tools.builtin.evolution.agent_manage import AgentManageTool
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.agent.tools.builtin.team.team_tools import (
    SendMessageTool,
    TeamFinishTool,
    TeamMemberCloseTool,
    TeamMemberCreateTool,
    TeamMemberRestartTool,
    TeamMemberStopRequestTool,
    TeamTaskClaimTool,
    TeamTaskCompleteTool,
    TeamTaskCreateTool,
    TeamTaskListTool,
    TeamTaskUpdateTool,
)
from juice_agents.core.managers.team import TeamManager
from juice_agents.core.managers.tools import ToolManager
from juice_agents.core.registry import AgentRegistry
from juice_agents.core.registry.tools.defaults import get_default_tool_factories
from juice_agents.core.team.builtin.configs import build_default_team_member_configs


def _bound(tool_type, agent):
    tool = tool_type()
    tool.bind_owner_agent(agent)
    return tool


def _runtime(tmp_path):
    manager = TeamManager(tmp_path / "team", member_names=("developer", "researcher"))
    runner = SimpleNamespace(mode_id="team", team_manager=manager, state={"team_run_id": "run-1"})
    root = SimpleNamespace()
    member = SimpleNamespace()
    root.runner_context = SimpleNamespace(runner=runner)
    member.runner_context = SimpleNamespace(runner=runner)
    root_record = SimpleNamespace(instance=root, agent_name="root", is_root=True, role="root")
    member_record = SimpleNamespace(
        instance=member, agent_name="developer", is_root=False, role="teammate",
        metadata={"team_member": "developer", "team_run_id": "run-1"},
    )
    runner.agent_manager = SimpleNamespace(live_agents={"root": root_record, "developer": member_record})
    runner.agent_registry = SimpleNamespace(load_config=lambda name: SimpleNamespace(
        name=name, allowed_modes=("team",), lifecycle="persistent"
    ))
    runner.create_team_member = lambda name, agent_name=None, config=None: manager.create_member(
        name, agent_name or config["name"]
    )
    runner.restart_team_member = lambda name: manager.restart_member(name)
    runner.close_team_member = lambda name: manager.close_member(name)
    runner.finish_team = lambda: manager.finish(requester="root")
    return manager, runner, root, member


def test_team_tools_task_and_message_flow(tmp_path):
    manager, _, root, member = _runtime(tmp_path)
    created = _bound(TeamTaskCreateTool, root).forward("Implement parser", eligible_members=["developer"])["task"]
    task_id = created["task_id"]
    assert _bound(TeamTaskListTool, member).forward()["tasks"][0]["task_id"] == task_id
    assert _bound(TeamTaskClaimTool, member).forward()["task"]["claimed_by"] == "developer"
    assert manager.list_tasks()[0]["status"] == "in_progress"
    sent = _bound(SendMessageTool, member).forward("root", "Parser is ready")["message"]
    assert manager.pending_messages("root")[0]["message_id"] == sent["message_id"]
    completed = _bound(TeamTaskCompleteTool, member).forward(task_id, "tests pass")["task"]
    assert completed["status"] == "completed"
    with pytest.raises(ValueError, match="pending messages"):
        _bound(TeamFinishTool, root).forward()
    manager.ack_messages("root", [sent["message_id"]])
    assert _bound(TeamFinishTool, root).forward()["team"]["finished"] is True


def test_team_tools_member_management_and_live_identity(tmp_path):
    manager, runner, root, member = _runtime(tmp_path)
    created = _bound(TeamMemberCreateTool, root).forward("reviewer", agent_name="researcher")["member"]
    assert created["agent_name"] == "researcher"
    assert _bound(TeamMemberRestartTool, root).forward("reviewer")["member"]["generation"] == 2
    assert _bound(TeamMemberCloseTool, root).forward("reviewer")["member"]["status"] == "closed"
    with pytest.raises(PermissionError, match="Only Team root"):
        _bound(TeamMemberCreateTool, member).forward("impostor")
    with pytest.raises(PermissionError, match="active AgentManager"):
        _bound(SendMessageTool, SimpleNamespace(runner_context=SimpleNamespace(runner=runner))).forward("root", "hi")
    assert any(item["name"] == "reviewer" for item in manager.snapshot()["members"])


def test_root_can_discover_shared_member_sources_before_adding_members(tmp_path):
    _, runner, root, member = _runtime(tmp_path)
    runner.config_context = ConfigurationContext.from_workspace(tmp_path)
    runner.list_available_agents = lambda: {"agents": []}
    root.name = "root"
    member.name = "developer"
    agents = AgentRegistry(config_context=runner.config_context)
    agents.save_config({"name": "team_worker", "lifecycle": "persistent", "allowed_modes": ["team"]})
    agents.save_config({"name": "agent_worker", "lifecycle": "persistent", "allowed_modes": ["agent"]})

    listed = _bound(AgentsListTool, root).forward()["agents"]
    assert [item["name"] for item in listed] == ["team_worker"]
    assert _bound(AgentViewTool, root).forward("team_worker")["agent"]["config"]["name"] == "team_worker"
    # Read-only discovery must not expand the root's Agent write authority.
    with pytest.raises(PermissionError, match="直属管理作用域"):
        _bound(AgentManageTool, root)._resolve_target("team_worker", "authorized")
    assert _bound(AgentsListTool, member).forward()["agents"] == []


def test_only_root_can_create_or_update_tasks(tmp_path):
    manager, _, root, member = _runtime(tmp_path)
    with pytest.raises(PermissionError, match="Only Team root"):
        _bound(TeamTaskCreateTool, member).forward("Unauthorized", eligible_members="all")
    task = _bound(TeamTaskCreateTool, root).forward("Recover parser", eligible_members=["developer"])["task"]
    _bound(TeamTaskClaimTool, member).forward(task["task_id"])
    manager.fail_task(task["task_id"], "developer", error="cancelled")
    assert _bound(TeamTaskUpdateTool, root).forward(task["task_id"], status="pending")["task"]["status"] == "pending"
    with pytest.raises(PermissionError, match="Only Team root"):
        _bound(TeamTaskUpdateTool, member).forward(task["task_id"], status="completed")
    with pytest.raises(PermissionError, match="Team root cannot execute"):
        _bound(TeamTaskClaimTool, root).forward(task["task_id"])


def test_member_from_another_team_run_cannot_use_current_board(tmp_path):
    _, runner, _, member = _runtime(tmp_path)
    record = runner.agent_manager.live_agents["developer"]
    record.metadata = {"team_member": "developer", "team_run_id": "old-run"}

    with pytest.raises(PermissionError, match="current Team run"):
        _bound(TeamTaskListTool, member).forward()


def test_member_stop_request_is_structured_and_does_not_close_member(tmp_path):
    manager, _, root, member = _runtime(tmp_path)
    ordinary = _bound(SendMessageTool, member).forward("root", "Please stop me")["message"]
    requested = _bound(TeamMemberStopRequestTool, member).forward("Work is blocked")["message"]

    assert ordinary.get("kind") != "member_stop_request"
    assert requested["kind"] == "member_stop_request"
    assert requested["member_name"] == "developer"
    assert requested["to"] == "root"
    assert next(item for item in manager.snapshot()["members"] if item["name"] == "developer")["status"] == "active"
    with pytest.raises(PermissionError, match="Only a Team member"):
        _bound(TeamMemberStopRequestTool, root).forward("not allowed")


def test_team_tool_registration_and_prompt_scope(tmp_path):
    factories = get_default_tool_factories()
    assert factories["send_message"] is SendMessageTool
    assert "team_member_sources" not in factories
    team_root = build_root_config(mode_id="team", plan_file=tmp_path / "plan.md")
    agent_root = build_root_config(mode_id="agent", plan_file=tmp_path / "plan.md")
    assert "team_finish" in {tool.name for tool in team_root.tools}
    assert "team_member_sources" not in {tool.name for tool in team_root.tools}
    assert "agent_tool" not in {tool.name for tool in team_root.tools}
    assert "team_finish" not in {tool.name for tool in agent_root.tools}
    workers = build_default_team_member_configs(workspace_dir=tmp_path)
    assert all(worker.lifecycle == "persistent" for worker in workers)
    assert all("send_message" in {tool.name for tool in worker.tools} for worker in workers)
    assert all("team_finish" not in {tool.name for tool in worker.tools} for worker in workers)
    assert all("team_task_create" not in {tool.name for tool in worker.tools} for worker in workers)
    assert all("team_task_update" not in {tool.name for tool in worker.tools} for worker in workers)
    assert all("team_member_stop_request" in {tool.name for tool in worker.tools} for worker in workers)


def test_team_tool_executes_through_tool_manager(tmp_path):
    _, _, root, _ = _runtime(tmp_path)
    tool_manager = ToolManager([_bound(TeamTaskCreateTool, root)], state_dir=tmp_path / "audits")
    try:
        result = tool_manager.execute_action({
            "name": "team_task_create", "args": {"title": "Check parser", "eligible_members": "all"},
        })
        assert result.status == "completed"
        assert result.observation["task"]["title"] == "Check parser"
        assert tool_manager.audit_path is not None and tool_manager.audit_path.is_file()
    finally:
        tool_manager.release()
