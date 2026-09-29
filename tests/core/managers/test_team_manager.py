"""Durable Team board, inbox, and member lifecycle contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from juice_agents.core.managers import TeamManager


def test_task_dependencies_and_atomic_claim_across_manager_instances(tmp_path: Path) -> None:
    state_dir = tmp_path / "team"
    first = TeamManager(state_dir, ["alice", "bob"])
    second = TeamManager(state_dir, ["alice", "bob"])
    preparation = first.add_task("prepare", eligible_members=["alice"])
    dependent = first.add_task(
        "review", eligible_members=["bob"], dependencies=[preparation["task_id"]]
    )
    assert preparation["status"] == "pending"
    assert preparation["eligible_members"] == ["alice"]
    assert preparation["claimed_by"] == ""
    with pytest.raises(ValueError, match="dependencies"):
        first.claim_task(dependent["task_id"], "bob")
    with pytest.raises(ValueError, match="does not exist"):
        first.add_task("invalid", dependencies=["missing"])

    # Two independent instances read the same file and race for one task.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda manager: manager.claim_next("alice"), (first, second)))
    assert sorted(item is not None for item in results) == [False, True]
    assert results[0] is None or results[0]["task_id"] == preparation["task_id"]
    assert first.list_tasks()[0]["status"] == "in_progress"
    first.complete_task(preparation["task_id"], "alice", result="ready")
    assert second.claim_next("bob")["task_id"] == dependent["task_id"]


def test_eligible_members_are_fixed_when_task_is_created(tmp_path: Path) -> None:
    manager = TeamManager(tmp_path / "team", ["alice", "bob"])
    everyone = manager.add_task("review", eligible_members="all")
    assert everyone["eligible_members"] == ["alice", "bob"]
    assert "root" not in everyone["eligible_members"]

    # Membership changes do not silently widen access to an existing task.
    manager.create_member("charlie", agent_name="reviewer")
    with pytest.raises(ValueError, match="eligible|资格|member"):
        manager.claim_task(everyone["task_id"], "charlie")
    assert manager.claim_next("charlie") is None

    restricted = manager.add_task("test", eligible_members=["charlie"])
    assert restricted["eligible_members"] == ["charlie"]
    assert manager.claim_task(restricted["task_id"], "charlie")["claimed_by"] == "charlie"
    with pytest.raises(ValueError, match="Unknown name"):
        manager.add_task("invalid", eligible_members=["missing"])
    with pytest.raises(ValueError, match="eligible|资格|empty|非空"):
        manager.add_task("invalid", eligible_members=[])


def test_messages_are_durable_until_ack_and_reconcile_is_idempotent(tmp_path: Path) -> None:
    events: list[dict] = []
    manager = TeamManager(tmp_path / "team", ["alice"], on_change=events.append)
    outgoing = manager.send_message("root", "alice", "Please investigate")
    incoming = manager.send_message("alice", "root", "I found it")
    restored = TeamManager(tmp_path / "team", ["alice"])
    assert [item["message_id"] for item in restored.pending_messages("alice")] == [outgoing["message_id"]]
    assert [item["message_id"] for item in restored.pending_messages("root")] == [incoming["message_id"]]
    with pytest.raises(ValueError, match="does not belong"):
        manager.ack_messages("root", [outgoing["message_id"]])
    assert restored.reconcile_messages("alice", [outgoing["message_id"]])
    assert restored.reconcile_messages("alice", [outgoing["message_id"]]) == []
    manager.ack_messages("alice", [outgoing["message_id"]])
    assert len(events) == 2  # A repeated ACK neither writes nor emits an update.
    assert restored.pending_messages("alice") == []
    assert events[0]["update"]["type"] == "message_sent"
    assert events[0]["snapshot"]["messages"][0]["message_id"] == outgoing["message_id"]


def test_member_stop_request_needs_explicit_root_action(tmp_path: Path) -> None:
    manager = TeamManager(tmp_path / "team", ["alice"])
    ordinary = manager.send_message("alice", "root", "Stop my work")
    requested = manager.request_member_stop("alice", "No more work")

    assert ordinary.get("kind") != "member_stop_request"
    assert requested["kind"] == "member_stop_request"
    assert requested["member_name"] == "alice"
    assert [item["message_id"] for item in manager.pending_messages("root")] == [
        ordinary["message_id"], requested["message_id"],
    ]
    assert manager.snapshot()["members"][1]["status"] == "active"
    with pytest.raises(ValueError, match="reason"):
        manager.request_member_stop("alice", "")


def test_member_lifecycle_and_finish_gates(tmp_path: Path) -> None:
    manager = TeamManager(tmp_path / "team", ["alice"])
    member = manager.create_member("bob", agent_name="researcher")
    assert member["agent_name"] == "researcher"
    assigned = manager.add_task("write", eligible_members=["bob"])
    assert manager.claim_next("alice") is None
    assert manager.claim_next("bob")["task_id"] == assigned["task_id"]
    manager.set_member_busy("bob", True)
    with pytest.raises(ValueError, match="busy"):
        manager.close_member("bob")
    with pytest.raises(ValueError, match="busy"):
        manager.restart_member("bob")
    manager.set_member_busy("bob", False)
    redirected = manager.send_message("root", "bob", "handoff")
    manager.close_member("bob")
    redirected_inbox = manager.pending_messages("root")
    assert redirected_inbox[0]["message_id"] == redirected["message_id"]
    assert redirected_inbox[0]["original_recipient"] == "bob"
    manager.ack_messages("root", [redirected["message_id"]])
    returned = manager.list_tasks()[0]
    assert returned["status"] == "in_progress"
    assert returned["claimed_by"] == "bob"
    assert returned["error"] == "member_closed"
    with pytest.raises(ValueError, match="closed"):
        manager.send_message("root", "bob", "too late")
    restarted = manager.restart_member("bob")
    assert restarted["generation"] == 2
    assert restarted["status"] == "active"
    with pytest.raises(ValueError, match="unfinished"):
        manager.finish()
    manager.update_task(assigned["task_id"], status="pending")
    manager.claim_task(assigned["task_id"], "bob")
    manager.complete_task(assigned["task_id"], "bob")
    pending = manager.send_message("bob", "root", "done")
    with pytest.raises(ValueError, match="pending messages"):
        manager.finish()
    manager.ack_messages("root", [pending["message_id"]])
    with pytest.raises(ValueError, match="Only Team root"):
        manager.finish("bob")
    assert manager.finish()["finished"] is True
    assert TeamManager(tmp_path / "team", ["alice"]).snapshot()["finished"] is True
    with pytest.raises(ValueError, match="finished"):
        manager.add_task("late")


def test_failure_requires_root_review_and_reassignment(tmp_path: Path) -> None:
    manager = TeamManager(tmp_path / "team", ["alice", "bob"])
    task = manager.add_task("investigate", eligible_members=["alice"])
    manager.claim_task(task["task_id"], "alice")
    manager.set_member_busy("alice", True)
    with pytest.raises(ValueError, match="running"):
        manager.update_task(task["task_id"], eligible_members=["bob"])
    with pytest.raises(ValueError, match="running"):
        manager.update_task(task["task_id"], delete=True)
    manager.set_member_busy("alice", False)

    failed = manager.fail_task(task["task_id"], "alice", error="tests failed")
    assert failed["status"] == "in_progress"
    assert failed["claimed_by"] == "alice"
    assert failed["error"] == "tests failed"
    assert manager.claim_next("bob") is None
    redirected = manager.update_task(task["task_id"], eligible_members=["bob"])
    assert redirected["status"] == "pending"
    assert redirected["claimed_by"] == ""
    assert manager.claim_task(task["task_id"], "bob")["status"] == "in_progress"


def test_recovery_keeps_failed_claim_for_root_review(tmp_path: Path) -> None:
    manager = TeamManager(tmp_path / "team", ["alice"])
    task = manager.add_task("investigate")
    manager.claim_task(task["task_id"], "alice")
    manager.set_member_busy("alice", True)
    restored = TeamManager(tmp_path / "team", ["alice"])
    recovered = restored.recover()
    assert recovered["members"][1]["busy"] is False
    assert restored.list_tasks()[0]["status"] == "in_progress"
    assert restored.list_tasks()[0]["claimed_by"] == "alice"
    assert restored.list_tasks()[0]["error"] == "stale_on_recovery"
    assert restored.claim_next("alice") is None
    restored.update_task(task["task_id"], status="pending")
    assert restored.claim_next("alice")["task_id"] == task["task_id"]
    restored.fail_task(task["task_id"], "alice", error="cancelled")
    assert restored.list_tasks()[0]["status"] == "in_progress"
    assert restored.has_pending_work() is True
