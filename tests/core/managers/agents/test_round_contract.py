"""Public lifecycle contracts through the real Manager and Runner boundaries."""

from __future__ import annotations

from copy import deepcopy
import json
import threading

import pytest

from juice_agents import RoundCancelledError, RoundFailedError, RoundResult
from juice_agents.core.agent.context import ActionEvent, ActionStep
from juice_agents.core.managers.agents import AgentStatus
from tests.core.managers.agents.test_durable_inbox import Registry, attachment, manager_at, wait_for
from tests.core.runner.test_persistent_rounds import _runner


@pytest.mark.parametrize("lifecycle", ["persistent", "functional"])
@pytest.mark.parametrize("root", [True, False])
@pytest.mark.parametrize("outcome", ["submitted", "failed"])
def test_result_and_instance_lifetime_are_independent(tmp_path, lifecycle, root, outcome):
    def action(_agent, step):
        step.round_outcome, step.output = outcome, {"partial": "retained"}
        return step

    events = []
    manager = manager_at(tmp_path, Registry(action), events=events)
    item = manager.acquire({"name": "worker"}, lifecycle=lifecycle, is_root=root)
    try:
        result = manager.run(item, "work")
        assert isinstance(result, RoundResult)
        assert result.status == ("completed" if outcome == "submitted" else "failed")
        assert result.output == {"partial": "retained"}
        assert item.status is (AgentStatus.IDLE if lifecycle == "persistent" else AgentStatus.RELEASED)
        final = [event for event in events if event["kind"] == "round_end"]
        assert len(final) == 1
        assert final[0]["data"]["result"] == result.to_dict()
        assert manager.load_snapshot(item.agent_id)["session"]["round_checkpoint"]["result"] == result.to_dict()
        assert not manager.cancel_round(result.round_id)
        assert manager._result(item) == result
        for event in events:
            assert {"kind", "data", "event_id", "timestamp", "runner_id", "agent_id", "agent_name", "agent_role"} <= event.keys()
            assert "action_step" not in event and "event_type" not in event
    finally:
        manager.close()


@pytest.mark.parametrize("submit_last", [False, True])
def test_last_submit_opportunity_and_partial_budget_result(tmp_path, submit_last):
    def action(_agent, step):
        step.model_output = f"partial {step.step_num}"
        if submit_last and step.step_num == 3:
            step.round_outcome, step.output = "submitted", "last opportunity"
        return step

    registry = Registry(action)
    manager = manager_at(tmp_path, registry)
    item = manager.acquire_root({"name": "root"})
    try:
        result = manager.run(item, "work")
        assert registry.instances[0].calls == [1, 2, 3]
        assert result.status == ("completed" if submit_last else "failed")
        assert result.reason == ("submitted" if submit_last else "max_steps_exceeded")
        assert result.output == ("last opportunity" if submit_last else "partial 3")
    finally:
        manager.close()


def test_cancel_intent_precedes_unwind_and_every_started_boundary_ends(tmp_path):
    entered, unwind = threading.Event(), threading.Event()

    class ToolAgent:
        def __init__(self):
            from juice_agents.core.agent.context import ContextTree
            self.context, self.max_steps = ContextTree(tree_id="tools", system_prompt="test"), 2

        def step(self, step):
            yield ActionEvent(step.step_num, "tool_start", {"call_id": "tool-1", "tool_name": "wait"})
            entered.set()
            assert self.cancel_event.wait(3)
            assert unwind.wait(3)
            yield ActionEvent(step.step_num, "tool_end", {"call_id": "tool-1", "tool_name": "wait", "status": "cancelled"})
            raise RuntimeError("provider cancelled")

    registry = Registry()
    registry.instantiate = lambda *_a, **_k: ToolAgent()
    events = []
    manager = manager_at(tmp_path, registry, events=events)
    item = manager.acquire_root({"name": "root"})
    receipt = manager.post(item.agent_id, attachment("input"))
    try:
        manager.activate()
        assert entered.wait(3)
        assert manager.cancel_round(receipt["round_id"])
        saved = manager.load_snapshot(item.agent_id)
        checkpoint = saved["session"]["round_checkpoint"]
        assert checkpoint["status"] == "active" and checkpoint["cancel_requested"]
        assert item.status is AgentStatus.RUNNING
        assert not [event for event in events if event["kind"] == "round_end"]
        unwind.set()
        wait_for(lambda: any(event["kind"] == "round_end" for event in events))
        kinds = [event["kind"] for event in events]
        assert kinds.count("step_start") == kinds.count("step_end") == 1
        assert kinds.count("tool_start") == kinds.count("tool_end") == 1
        assert kinds.index("tool_end") < kinds.index("step_end") < kinds.index("round_end")
        assert events[-1]["data"]["result"]["status"] == "cancelled"
    finally:
        unwind.set()
        manager.close()


def test_closing_a_committed_or_unstarted_functional_stream_does_not_rewrite_or_leak(tmp_path):
    manager = manager_at(tmp_path, Registry())
    item = manager.acquire({"name": "worker"}, lifecycle="functional")
    stream = manager.stream(item, "work")
    try:
        assert isinstance(next(stream), ActionStep)
        result = deepcopy(item.round_checkpoint["result"])
        stream.close()
        assert item.status is AgentStatus.RELEASED
        assert item.round_checkpoint["result"] == result
        fresh = manager.acquire({"name": "worker"}, lifecycle="functional")
        manager.stream(fresh, "unstarted").close()
        assert fresh.status is AgentStatus.RELEASED
        assert not manager.live_agents
    finally:
        manager.close()


def test_private_workflow_agent_has_no_public_event_or_transcript(tmp_path):
    events = []
    manager = manager_at(tmp_path, Registry(), events=events)
    item = manager.acquire({"name": "private"}, lifecycle="functional", ephemeral=True)
    try:
        assert manager.run(item, "secret body").status == "completed"
        assert events == []
        assert manager.describe_sessions()["agents"] == []
        assert not (tmp_path / "agents" / item.agent_id).exists()
    finally:
        manager.close()


@pytest.mark.parametrize("lifecycle", ["persistent", "functional"])
def test_child_failure_raises_a_result_instead_of_returning_error_text(tmp_path, lifecycle):
    def action(agent, _step):
        if agent.name != "root":
            raise RuntimeError("child failed")

    runner = _runner(tmp_path, action)
    try:
        runner._root()
        with pytest.raises(RoundFailedError) as failure:
            runner.invoke_agent({"agent_ref": "general", "task": "work", "execution": "sync", "lifecycle": lifecycle})
        assert failure.value.result.status == "failed"
        assert failure.value.result.error == "child failed"
    finally:
        runner.close()


def test_sdk_run_raises_and_run_round_exposes_the_same_failed_result(tmp_path):
    runner = _runner(tmp_path, lambda *_: (_ for _ in ()).throw(RuntimeError("model unavailable")))
    try:
        with pytest.raises(RoundFailedError) as failure:
            runner.run("work")
        assert failure.value.result.error == "model unavailable"
        assert runner.run_round("retry").status == "failed"
        assert runner.run_round("cancel before start", cancel_check=lambda: True).status == "cancelled"
        with pytest.raises(RoundCancelledError):
            RoundResult("cancel", "cancelled", reason="user_cancelled").unwrap()
    finally:
        runner.close()


def test_old_snapshot_rejected_without_rewriting_the_file(tmp_path):
    manager = manager_at(tmp_path, Registry())
    path = tmp_path / "agents" / "root" / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "agent_id": "root"}))
    path.with_name("session.json").write_text("{}")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="不支持恢复"):
        manager.acquire_root({"name": "root"})
    assert path.read_bytes() == original


def test_resume_validates_agent_formats_before_any_runner_state_write(tmp_path):
    from juice_agents import Runner

    runner = _runner(tmp_path)
    runner.run("seed")
    runner.close()
    manifest = runner.layout.agents_dir / "root" / "manifest.json"
    payload = json.loads(manifest.read_text())
    payload["schema_version"] = 1
    manifest.write_text(json.dumps(payload))
    before = {path: path.read_bytes() for path in runner.root_dir.rglob("*.json")}
    with pytest.raises(ValueError, match="不支持恢复"):
        Runner.resume(base_dir=tmp_path, runner_id=runner.runner_id)
    assert {path: path.read_bytes() for path in before} == before


def test_secondary_inbox_cleanup_failure_cannot_hide_a_committed_result(tmp_path, monkeypatch):
    """The initial failed business ack keeps an entry for terminal cleanup."""
    attempts = 0

    def acknowledge(_item, _attachments):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("producer temporarily unavailable")

    events = []
    manager = manager_at(tmp_path, Registry(), events=events, acknowledge=acknowledge)
    root = manager.acquire_root({"name": "root"})
    receipt = manager.post(root.agent_id, attachment("input"))
    save_inbox = manager._save_inbox

    def cleanup_fails(agent_id, entries):
        if not entries and manager._result(root) is not None:
            raise OSError("inbox cleanup unavailable")
        save_inbox(agent_id, entries)

    monkeypatch.setattr(manager, "_save_inbox", cleanup_fails)
    try:
        manager.activate()
        wait_for(lambda: any(event["kind"] == "round_end" for event in events))
        result = manager._result(root)
        assert result.status == "completed" and result.round_id == receipt["round_id"]
        assert len([event for event in events if event["kind"] == "step_end"]) == 1
        assert len([event for event in events if event["kind"] == "round_end"]) == 1
        monkeypatch.setattr(manager, "_save_inbox", save_inbox)
        manager.activate()
        assert manager._load_inbox(root.agent_id) == []
        assert manager._result(root) == result
    finally:
        monkeypatch.setattr(manager, "_save_inbox", save_inbox)
        manager.close()


def test_reassigned_inputs_are_excluded_from_the_preceding_active_round(tmp_path):
    """Simulate process loss after durable deferral, before terminal save."""
    from juice_agents.core.agent.context import TaskStep

    seed = manager_at(tmp_path, Registry())
    root = seed.acquire_root({"name": "root"})
    root.context.append_step(TaskStep(task="old input", round_id="old-round"))
    root.round_checkpoint = {"round_id": "old-round", "status": "active", "next_step": 1, "max_steps": 2}
    seed.persist(root)
    receipt = seed.post(root.agent_id, attachment("next-input"))
    assert receipt["round_id"] == "old-round"
    seed._defer_inbox(root)
    next_round = seed._load_inbox(root.agent_id)[0]["round_id"]
    assert next_round != "old-round"
    assert seed._pending_attachments(root) == []

    events = []
    restored = manager_at(tmp_path, Registry(), events=events)
    try:
        recovered = restored.acquire_root({"name": "root"})
        assert restored._pending_attachments(recovered) == []
        restored.activate()
        wait_for(lambda: len([event for event in events if event["kind"] == "round_end"]) == 2)
        assert recovered.message_round_ids["next-input"] == next_round
        assert list(recovered.round_results) == ["old-round", next_round]
    finally:
        restored.close()
        # The seed represents the dead process and must not write old state.
        root.round_checkpoint["status"] = "completed"
        seed.close(wait=False)


def test_functional_terminal_release_needs_no_second_checkpoint(tmp_path, monkeypatch):
    events = []
    manager = manager_at(tmp_path, Registry(), events=events)
    item = manager.acquire({"name": "worker"}, lifecycle="functional")
    save = manager.snapshot_store.save
    terminal_saves = []

    def save_once(snapshot):
        if snapshot["session"]["round_checkpoint"].get("result"):
            terminal_saves.append(snapshot["status"])
            if len(terminal_saves) > 1:
                raise OSError("a second terminal write is unavailable")
        return save(snapshot)

    monkeypatch.setattr(manager.snapshot_store, "save", save_once)
    try:
        result = manager.run(item, "work")
        assert result.status == "completed"
        assert terminal_saves == ["released"]
        assert item.instance is None and not manager.live_agents
        assert [event for event in events if event["kind"] == "round_end"][0]["data"]["result"] == result.to_dict()
    finally:
        manager.close()


def test_cold_history_retains_failed_cancelled_and_later_successful_rounds(tmp_path):
    def action(agent, step):
        step.round_outcome = "failed" if len(agent.calls) == 1 else "submitted"
        step.output = "partial" if len(agent.calls) == 1 else "done"
        return step

    manager = manager_at(tmp_path, Registry(action))
    item = manager.acquire_root({"name": "root"})
    failed = manager.run(item, "fails")
    receipt = manager.post(item.agent_id, attachment("cancelled-before-start"))
    assert manager.cancel_round(receipt["round_id"])
    success = manager.run(item, "succeeds")
    manager.close()
    cold = manager_at(tmp_path, Registry())
    try:
        report = cold.describe_sessions(agent_id="root")["agents"][0]
        assert report["agent_role"] == "root" and report["agent_kind"] == "persistent"
        outcomes = [step for step in report["steps"] if step["type"] == "round_result"]
        assert [(step["round_id"], step["result"]["status"]) for step in outcomes] == [
            (failed.round_id, "failed"), (receipt["round_id"], "cancelled"), (success.round_id, "completed")]
        assert report["session"]["round_results"][failed.round_id]["output"] == "partial"
        assert not cold.live_agents
    finally:
        cold.close()


def test_closed_manager_rejects_acquisition_and_an_unstarted_execution(tmp_path):
    manager = manager_at(tmp_path, Registry())
    item = manager.acquire({"name": "worker"}, lifecycle="functional")
    stream = manager.stream(item, "queued")
    manager.close()
    with pytest.raises(RuntimeError, match="已关闭"):
        manager.acquire({"name": "new-worker"})
    with pytest.raises(RuntimeError, match="已关闭"):
        next(stream)


def test_after_persist_observer_cannot_rewrite_or_hide_success(tmp_path):
    from dataclasses import replace

    events = []
    manager = manager_at(tmp_path, Registry(), events=events)
    item = manager.acquire_root({"name": "root"})

    def observer(snapshot):
        if snapshot["session"]["round_checkpoint"].get("result"):
            raise RuntimeError("observer unavailable")

    manager.callbacks = replace(manager.callbacks, after_persist=observer)
    try:
        result = manager.run(item, "work")
        assert result.status == "completed"
        assert manager.load_snapshot(item.agent_id)["session"]["round_results"][result.round_id] == result.to_dict()
        assert len([event for event in events if event["kind"] == "round_end"]) == 1
    finally:
        manager.close()


@pytest.mark.parametrize("failed_file", ["manifest.json", "session.json"])
@pytest.mark.parametrize("lifecycle", ["persistent", "functional"])
def test_partial_checkpoint_write_cannot_publish_an_uncommitted_completion(tmp_path, monkeypatch, failed_file, lifecycle):
    armed = False

    def action(_agent, step):
        nonlocal armed
        armed = True
        step.round_outcome, step.output = "submitted", "generated output"
        return step

    events = []
    manager = manager_at(tmp_path, Registry(action), events=events)
    item = manager.acquire({"name": "worker"}, lifecycle=lifecycle)
    store = manager.snapshot_store
    atomic_write = store._atomic_write
    failures = []

    def fail_once(path, payload):
        if armed and path.name == failed_file and not failures:
            failures.append(path.name)
            # A manifest can lead the session, but cannot make an uncommitted
            # idle/released state or completed result authoritative.
            durable = store.load(item.agent_id)
            assert durable["status"] == "running"
            assert "result" not in durable["session"]["round_checkpoint"]
            raise OSError("injected checkpoint failure")
        atomic_write(path, payload)

    monkeypatch.setattr(store, "_atomic_write", fail_once)
    try:
        result = manager.run(item, "work")
        assert result.status == "failed"
        assert result.error == "injected checkpoint failure"
        assert failures == [failed_file]
        final_events = [event for event in events if event["kind"] == "round_end"]
        assert len(final_events) == 1 and final_events[0]["data"]["result"] == result.to_dict()
        durable = store.load(item.agent_id)
        assert durable["session"]["round_results"][result.round_id] == result.to_dict()
        assert durable["status"] == ("idle" if lifecycle == "persistent" else "released")
    finally:
        manager.close()


def test_ahead_manifest_cannot_replace_the_last_committed_session(tmp_path, monkeypatch):
    manager = manager_at(tmp_path, Registry())
    item = manager.acquire_root({"name": "root"})
    result = manager.run(item, "first")
    saved = manager.load_snapshot(item.agent_id)
    incoming = deepcopy(saved)
    incoming["status"] = "running"
    incoming["session"]["agent_status"] = "running"
    incoming["session"]["round_checkpoint"] = {"round_id": "uncommitted", "status": "active"}
    store = manager.snapshot_store
    atomic_write = store._atomic_write

    def fail_session(path, payload):
        if path.name == "session.json":
            raise OSError("session unavailable")
        atomic_write(path, payload)

    try:
        monkeypatch.setattr(store, "_atomic_write", fail_session)
        with pytest.raises(OSError, match="session unavailable"):
            store.save(incoming)
        durable = manager.load_snapshot(item.agent_id)
        assert durable["status"] == "idle"
        assert durable["session"]["round_checkpoint"]["result"] == result.to_dict()
    finally:
        monkeypatch.setattr(store, "_atomic_write", atomic_write)
        manager.close()


def test_previous_success_does_not_mask_rejected_new_execution(tmp_path):
    manager = manager_at(tmp_path, Registry())
    item = manager.acquire_root({"name": "root"})
    assert manager.run(item, "first").status == "completed"
    with pytest.raises(ValueError, match="task 不能为空"):
        manager.run(item, "")
    manager.close()
    with pytest.raises(RuntimeError, match="已关闭"):
        manager.run(item, "after close")
