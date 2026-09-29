"""The WebSocket stream forwards Team changes with their persisted snapshot."""

from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from adapters.web_gateway.app import WebSocketRuntimeConnection, create_app
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry import AgentRegistry


def test_websocket_stream_forwards_team_update() -> None:
    connection = WebSocketRuntimeConnection(websocket=MagicMock())
    runtime = MagicMock()
    runtime.stream_message.return_value = iter(
        [
            {
                "kind": "team_update",
                "runner_id": "run-1",
                "mode_id": "team",
                "team_name": "alpha",
                "update": {"type": "member_started", "member_name": "writer"},
                "snapshot": {
                    "tasks": [{
                        "task_id": "task-1",
                        "status": "in_progress",
                        "eligible_members": ["writer"],
                        "claimed_by": "writer",
                        "error": "",
                    }],
                    "members": [{"name": "writer", "status": "working"}],
                    "finished": False,
                },
            }
        ]
    )
    connection.runtime = runtime
    emitted = []
    connection._put_threadsafe = emitted.append

    connection._run_stream_worker("request-1", {"message": "start"})

    assert len(emitted) == 1
    assert emitted[0]["id"] == "request-1"
    assert emitted[0]["type"] == "stream_step"
    assert emitted[0]["payload"]["kind"] == "team_update"
    assert emitted[0]["payload"]["team_event"]["snapshot"]["members"] == [
        {"name": "writer", "status": "working"}
    ]
    assert emitted[0]["payload"]["team_event"]["snapshot"]["tasks"][0]["claimed_by"] == "writer"


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
            {"name": "reviewer", "agent_type": "react", "max_steps": 3, "tools": []}
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
