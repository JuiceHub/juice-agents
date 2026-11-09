"""The WebSocket stream forwards Team changes with their persisted snapshot."""

from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from adapters.web_gateway.app import WebSocketRuntimeConnection, create_app
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry import AgentRegistry


def test_websocket_subscription_forwards_team_update() -> None:
    connection = WebSocketRuntimeConnection(websocket=MagicMock())
    emitted = []
    connection._put_threadsafe = emitted.append
    data = {
        "team_name": "alpha",
        "update": {"type": "member_started", "member_name": "writer"},
        "snapshot": {
            "tasks": [{"task_id": "task-1", "status": "in_progress", "claimed_by": "writer"}],
            "members": [{"name": "writer", "status": "working"}],
            "finished": False,
        },
    }
    connection._publish_runner_event({
        "kind": "team_update", "event_id": "team-1", "timestamp": 123.5,
        "runner_id": "run-1", "agent_id": "root-1", "agent_name": "root",
        "agent_role": "root", "round_id": "round-1", "data": data,
    })
    assert len(emitted) == 1
    assert "id" not in emitted[0]
    assert emitted[0]["type"] == "stream_step"
    assert emitted[0]["payload"]["data"] == data


def test_web_team_config_defaults_to_empty_members() -> None:
    with TemporaryDirectory() as workspace:
        client = TestClient(create_app(), base_url="http://localhost")
        created = client.post("/api/teams/configs", json={"base_dir": workspace, "team_name": "empty"})
        fetched = client.get("/api/teams/configs/empty", params={"base_dir": workspace})

        assert created.status_code == 200
        assert fetched.json()["member_names"] == []


def test_web_team_config_preserves_shared_member_source() -> None:
    with TemporaryDirectory() as workspace:
        AgentRegistry(config_context=ConfigurationContext.from_workspace(workspace)).save_config(
            {"name": "reviewer", "agent_type": "react", "max_steps": 3, "tools": [], "lifecycle": "persistent"}
        )
        client = TestClient(create_app(), base_url="http://localhost")
        created = client.post("/api/teams/configs", json={
            "base_dir": workspace,
            "team_name": "review",
            "member_names": ["reviewer"],
            "shared_agent_names": {"reviewer": "reviewer"},
        })
        fetched = client.get("/api/teams/configs/review", params={"base_dir": workspace})

        assert created.status_code == 200
        assert fetched.json()["shared_agent_names"] == {"reviewer": "reviewer"}
