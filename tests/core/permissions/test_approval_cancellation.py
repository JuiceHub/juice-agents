"""Real tool approvals keep their execution and cancellation scopes at gateways."""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from adapters.stdio_gateway.stdio_server import StdioJsonRpcServer
from adapters.web_gateway.app import WebSocketRuntimeConnection
from juice_agents.core.permissions.policy import PermissionAction, PermissionDenied, PermissionEngine
from juice_agents.core.registry.agents import registry as registry_module


class _ApprovalModel:
    """Each real Agent attempts one write, then submits after the observation."""

    def __init__(self):
        self.calls = {}
        self.lock = threading.Lock()

    def generate(self, messages, **_kwargs):
        content = json.dumps(messages, ensure_ascii=False, default=str)
        marker = next(name for name in ("approval-background", "approval-sync", "approval-root") if name in content)
        with self.lock:
            count = self.calls.get(marker, 0)
            self.calls[marker] = count + 1
        action = (
            {"name": "write", "args": {"path": f"{marker}.txt", "content": "approved"}}
            if count == 0 else {"name": "submit_output", "args": {"output": marker}}
        )
        return {"content": f"<thought>perform requested operation</thought><actions>{json.dumps([action])}</actions>"}


@pytest.mark.parametrize("gateway", ["stdio", "web"])
def test_real_permission_cancellation_scopes_root_sync_and_background(tmp_path, monkeypatch, gateway):
    model = _ApprovalModel()
    monkeypatch.setattr(registry_module, "resolve_runtime_model", lambda **_kwargs: model)
    requests, events, responses = [], [], []
    changed = threading.Condition()

    def receive(request):
        with changed:
            requests.append(request)
            changed.notify_all()

    if gateway == "stdio":
        endpoint = StdioJsonRpcServer()
        monkeypatch.setattr(endpoint, "_start_reader", lambda: None)

        def notify(message):
            if message.get("method") == "ask_request":
                receive(message["params"]["request"])
            elif message.get("method") == "stream_step":
                events.append(message["params"]["event"])
            else:
                responses.append(message)

        monkeypatch.setattr(endpoint, "_write_json", notify)
        endpoint.handlers.dispatch("start_session", {"base_dir": str(tmp_path)})
        runtime = endpoint.handlers._runtime

        def cancel(payload):
            endpoint._handle_request({"id": "cancel", "method": "cancel_stream", "params": payload})

        def answer(request_id):
            endpoint._route_ask_answer({"id": "approve", "params": {"request_id": request_id, "response": approval}})
    else:
        endpoint = WebSocketRuntimeConnection(SimpleNamespace())

        def notify(message):
            if message["type"] == "ask_request":
                receive(message["payload"])
            elif message["type"] == "stream_step":
                events.append(message["payload"])

        async def send_result(command_id, payload):
            responses.append({"id": command_id, "result": payload})

        monkeypatch.setattr(endpoint, "_put_threadsafe", notify)
        monkeypatch.setattr(endpoint, "_send_result", send_result)
        endpoint._handle_sync_command("start_session", {"base_dir": str(tmp_path)})
        runtime = endpoint.runtime

        def cancel(payload):
            asyncio.run(endpoint._cancel_stream("cancel", payload))

        def answer(request_id):
            asyncio.run(endpoint._answer_ask("approve", {"request_id": request_id, "response": approval}))

    runner = runtime._runner
    approval = {"status": "answered", "selected": [{"label": "允许一次", "value": "allow_once", "description": ""}]}
    results, errors = {}, {}

    def execute(name, operation):
        try:
            results[name] = operation()
        except Exception as exc:
            errors[name] = exc

    def wait_requests(count):
        with changed:
            assert changed.wait_for(lambda: len(requests) >= count, timeout=5), (requests, errors)

    root = threading.Thread(target=execute, args=("root", lambda: runner.run_round("approval-root")), daemon=True)
    sync = threading.Thread(target=execute, args=("sync", lambda: runner.invoke_agent({
        "agent_ref": "general", "task": "approval-sync", "execution": "sync", "lifecycle": "functional",
    })), daemon=True)
    try:
        root.start()
        wait_requests(1)
        sync.start()
        wait_requests(2)
        background = runner.invoke_agent({"agent_ref": "general", "task": "approval-background", "execution": "background", "lifecycle": "functional"})
        wait_requests(3)
        root_ask, sync_ask, background_ask = requests
        root_round = root_ask["round_id"]
        assert all(request["runner_id"] == runner.runner_id for request in requests)
        assert len({request["agent_id"] for request in requests}) == 3
        assert root_ask["cancellation_round_id"] == root_round
        assert sync_ask["round_id"] != root_round
        assert sync_ask["cancellation_round_id"] == root_round
        assert background_ask["cancellation_round_id"] == background_ask["round_id"] != root_round

        cancel({"runner_id": runner.runner_id, "round_id": root_round})
        assert next(response for response in responses if response.get("id") == "cancel")["result"] == {"cancelled": True}
        root.join(5)
        sync.join(5)
        assert not root.is_alive() and not sync.is_alive(), "cancelled approvals must release their real execution workers"
        assert "root" not in errors and results["root"].status == "cancelled"
        assert errors["sync"].result.status == "cancelled"
        assert runner.get_async_task(background["async_task_id"])["status"] == "running"
        assert not runner.agent_manager.get(background["agent_id"]).cancel_event.is_set()

        # Unapproved writes never started; the independent approval still works.
        blocked_agents = {root_ask["agent_id"], sync_ask["agent_id"]}
        assert not any(event["kind"] == "tool_start" and event["agent_id"] in blocked_agents for event in events)
        answer(background_ask["request_id"])
        assert not runner.async_task_manager.wait_active_async_tasks(timeout_seconds=5)
        assert runner.get_async_task(background["async_task_id"])["status"] == "completed"
        assert (tmp_path / "approval-background.txt").read_text() == "approved"
        assert not (tmp_path / "approval-root.txt").exists()
        assert not (tmp_path / "approval-sync.txt").exists()
        assert len([event for event in events if event["kind"] == "tool_start" and event["agent_id"] == background["agent_id"] and event["data"]["tool_name"] == "write"]) == 1
    finally:
        runtime.stop_session()
        root.join(5)
        if sync.ident is not None:
            sync.join(5)


def test_standalone_permission_runner_can_still_receive_approval(tmp_path):
    received = []

    def ask(request):
        received.append(request)
        return {"status": "cancelled"}

    agent = SimpleNamespace(runner_context=SimpleNamespace(runner=SimpleNamespace(ask_user=ask)), runner_default_base_dir=tmp_path)
    with pytest.raises(PermissionDenied, match="未获得审批"):
        PermissionEngine(agent)._ask_user(PermissionAction("write", "file.txt", "", False))
    assert received[0]["request_id"].startswith("permission-")
