"""Team events preserve the same payload in stdio and WebSocket streams."""

from tempfile import TemporaryDirectory

from adapters.stdio_gateway.handlers import RpcHandlers
from adapters.stdio_gateway.serialization import serialize_runner_stream_event
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry import AgentRegistry


def test_team_update_serializes_snapshot_into_shared_wire_envelope() -> None:
    event = serialize_runner_stream_event(
        {
            "kind": "team_update",
            "runner_id": "run-1",
            "mode_id": "team",
            "team_name": "alpha",
            "agent_name": "root",
            "update": {"type": "task_completed", "task_id": "task-1"},
            "snapshot": {
                "tasks": [
                    {
                        "task_id": "task-1",
                        "title": "Review",
                        "eligible_members": ["writer", "reviewer"],
                        "claimed_by": "reviewer",
                        "status": "completed",
                        "dependencies": [],
                    }
                ],
                "members": [{"name": "writer", "status": "idle"}],
                "finished": False,
            },
        }
    )

    assert event["agent_mode"] == "team"
    assert event["actor_name"] == "root"
    assert event["team_event"] == {
        "team_name": "alpha",
        "actor": "root",
        "update": {"type": "task_completed", "task_id": "task-1"},
        "snapshot": {
            "tasks": [
                {
                    "task_id": "task-1",
                    "title": "Review",
                    "eligible_members": ["writer", "reviewer"],
                    "claimed_by": "reviewer",
                    "status": "completed",
                    "dependencies": [],
                }
            ],
            "members": [{"name": "writer", "status": "idle"}],
            "finished": False,
        },
    }


def test_team_update_keeps_existing_envelope() -> None:
    team_event = {"actor": "root", "update": {"phase": "running"}}
    assert serialize_runner_stream_event({"kind": "team_update", "team_event": team_event})["team_event"] == team_event


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
            {"name": "reviewer", "agent_type": "react", "max_steps": 3, "tools": []}
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
