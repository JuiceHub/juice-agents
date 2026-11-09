"""Team events preserve the same payload in stdio and WebSocket streams."""

from tempfile import TemporaryDirectory

from adapters.stdio_gateway.handlers import RpcHandlers
from adapters.stdio_gateway.serialization import serialize_runner_stream_event
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry import AgentRegistry


def test_team_update_preserves_public_data_without_a_second_envelope() -> None:
    data = {
        "team_name": "alpha", "actor": "root",
        "update": {"type": "task_completed", "task_id": "task-1"},
        "snapshot": {
            "tasks": [{"task_id": "task-1", "claimed_by": "reviewer", "status": "completed"}],
            "members": [{"name": "writer", "status": "idle"}],
            "finished": False,
        },
    }
    event = serialize_runner_stream_event({
        "kind": "team_update", "event_id": "event-1", "timestamp": 123.5,
        "runner_id": "run-1", "agent_name": "root", "agent_id": "root-1",
        "agent_role": "root", "round_id": "round-1", "data": data,
    })
    assert event["data"] == data
    assert event["agent_name"] == "root"
    assert "team_event" not in event


def test_stdio_team_config_defaults_to_empty_members() -> None:
    with TemporaryDirectory() as workspace:
        handlers = RpcHandlers()
        created = handlers.handle_create_team_config({"base_dir": workspace, "team_name": "empty"})
        manifest = handlers.handle_get_team_config({"base_dir": workspace, "team_name": "empty"})

        assert created["team_name"] == "empty"
        assert manifest["member_names"] == []


def test_stdio_team_config_preserves_shared_member_source() -> None:
    with TemporaryDirectory() as workspace:
        AgentRegistry(config_context=ConfigurationContext.from_workspace(workspace)).save_config(
            {"name": "reviewer", "agent_type": "react", "max_steps": 3, "tools": [], "lifecycle": "persistent", "allowed_modes": ["team"]}
        )
        handlers = RpcHandlers()
        handlers.handle_create_team_config({"base_dir": workspace, "team_name": "review", "member_names": ["reviewer"]})
        handlers.handle_update_team_manifest({
            "base_dir": workspace,
            "team_name": "review",
            "shared_agent_names": {"reviewer": "reviewer"},
        })

        manifest = handlers.handle_get_team_config({"base_dir": workspace, "team_name": "review"})
        assert manifest["shared_agent_names"] == {"reviewer": "reviewer"}
