"""会话展示读取完整选中路径；模型上下文压缩不应抹掉可回看的历史。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from juice_agents.core.agent.context import (
    ActionStep,
    ContextNode,
    ContextTree,
    NodeStatus,
    SummaryStep,
    TaskStep,
    serialize_context_tree,
)
from juice_agents.core.agent.context.serialization import serialize_context_transcript
from juice_agents.core.managers.agents import AgentManager
from juice_agents.core.runner.session_titles import extract_first_user_request_preview


def _conversation() -> ContextTree:
    tree = ContextTree(tree_id="history", system_prompt="system")
    tree.append_step(TaskStep(task="  first\n request  "))
    task_id = tree.active_leaf_ids[0]
    tree.append_step(ActionStep(step_num=1, model_output="", round_outcome="submitted", output="first answer"))
    # Deliberately give the rejected sibling a better score. Only the selected
    # ancestry belongs to the conversation, regardless of insertion or score.
    tree.add_node(ContextNode(
        node_id="rejected", status=NodeStatus.REJECTED, score=100,
        step=ActionStep(step_num=1, model_output="", round_outcome="submitted", output="discarded answer"),
    ), parent_id=task_id)
    tree.append_step(SummaryStep(content="compressed context"))
    tree.nodes[tree.active_leaf_ids[0]].annotations["context_reset"] = True
    tree.append_step(TaskStep(task="second request"))
    tree.append_step(ActionStep(step_num=1, model_output="", round_outcome="submitted", output="second answer"))
    return tree


@pytest.mark.parametrize("completed", [False, True])
def test_transcript_keeps_selected_history_before_compression(completed: bool) -> None:
    tree = _conversation()
    if completed:
        path = tree.finalize_path(tree.active_leaf_ids[0])
        tree.selected_path_id = path.path_id
    before = deepcopy(serialize_context_tree(tree))

    transcript = serialize_context_transcript(tree)

    assert [step["type"] for step in transcript] == ["task", "action", "summary", "task", "action"]
    assert [step["task"] for step in transcript if step["type"] == "task"] == [
        "  first\n request  ", "second request",
    ]
    assert [step["output"] for step in transcript if step["type"] == "action"] == [
        "first answer", "second answer",
    ]
    # Display must neither rewrite the tree nor undo model context compression.
    assert serialize_context_tree(tree) == before
    assert isinstance(tree.to_linear_steps()[0], SummaryStep)


def test_transcript_supports_empty_and_root_only_sessions() -> None:
    tree = ContextTree(tree_id="empty", system_prompt="system")
    assert serialize_context_transcript(tree) == []
    tree.append_step(TaskStep(task="pending request"))
    tree.active_leaf_ids.clear()
    assert serialize_context_transcript(tree)[0]["task"] == "pending request"


def test_session_preview_uses_original_request_after_compression() -> None:
    payload = serialize_context_tree(_conversation())
    before = deepcopy(payload)
    assert extract_first_user_request_preview(payload) == "first request"
    assert payload == before


@pytest.mark.parametrize("payload", [
    None, [], {}, {"nodes": []}, {"nodes": {"broken": None}},
    {"nodes": {"broken": {}}}, {"version": "unsupported"},
])
def test_invalid_or_empty_sessions_have_no_preview(payload: object) -> None:
    assert extract_first_user_request_preview(payload) == ""


def test_live_and_cold_reports_read_transcripts_without_acquiring_agents(tmp_path: Path) -> None:
    from tests.core.managers.agents.test_manager import _Registry

    registry = _Registry()
    manager = AgentManager(registry=registry, runner_id="history", state_dir=tmp_path)
    root = manager.acquire_root({"name": "root"}, session_state=_conversation())
    manager.persist(root)
    files_before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    calls_before = len(registry.instantiate_calls)
    live = manager.describe_sessions()
    cold_manager = AgentManager(registry=registry, runner_id="history", state_dir=tmp_path)
    cold = cold_manager.describe_sessions()

    assert live["agents"][0]["steps"] == cold["agents"][0]["steps"]
    assert [step["output"] for step in cold["agents"][0]["steps"] if step["type"] == "action"] == [
        "first answer", "second answer",
    ]
    assert not cold_manager.live_agents
    assert len(registry.instantiate_calls) == calls_before
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == files_before
    # The persisted format remains the tree; the flat list is only a UI export.
    snapshot = manager.load_snapshot(root.agent_id)
    assert snapshot is not None and "steps" not in snapshot["session"]
