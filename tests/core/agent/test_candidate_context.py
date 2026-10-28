"""Candidate durability and selected-branch projection through real Managers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from juice_agents.core.agent.context import (
    ActionEvent,
    ActionStep,
    ContextNode,
    ContextTree,
    ModelOutputProtocolError,
    NodeStatus,
    SummaryStep,
    TaskStep,
    deserialize_context_tree,
    serialize_context_tree,
)
from juice_agents.core.agent.context.compression import CompactCompressStrategy, RemainCompressStrategy
from juice_agents.core.agent.manager import AgentManager


class _CandidateAgent:
    """Emit the internal boundary before simulating one selected side effect."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.context = ContextTree(tree_id="candidates", system_prompt="system")
        self.max_steps = 2
        self.error = error
        self.selected_steps: list[ActionStep] = []
        self.executions = 0
        self.before_execution: Callable[[], None] = lambda: None

    def step(self, step: ActionStep):
        rejected = ActionStep(step_num=step.step_num, model_output="REJECTED", usage={"output_tokens": 11})
        rejected.error = ModelOutputProtocolError("invalid rejected proposal")
        step.model_output = "SELECTED"
        step.usage = {"output_tokens": 22}
        self.selected_steps.append(step)
        yield ActionEvent(
            step_num=step.step_num,
            event_type="candidates",
            data={"steps": [rejected, step], "scores": [None, 0.8], "selected_index": 1,
                  "strategy": "TreeOfThoughtsStrategy", "breadth": 2},
        )
        self.before_execution()
        if self.error is not None and len(self.selected_steps) == 1:
            raise self.error
        self.executions += 1
        step.observations = [f"executed:{self.executions}"]
        step.round_outcome = "submitted"
        step.output = f"answer:{self.executions}"
        yield ActionEvent(step_num=step.step_num, event_type="step_complete", data={"step": step})


class _Registry:
    def __init__(self, agent: _CandidateAgent) -> None:
        self.agent = agent

    def instantiate(self, binding, **kwargs):
        return self.agent


def _manager(tmp_path: Path, agent: _CandidateAgent):
    manager = AgentManager(registry=_Registry(agent), runner_id="candidates", state_dir=tmp_path)
    managed = manager.acquire_root({"name": "root"})
    return manager, managed


def test_manager_checkpoints_candidates_before_execution_and_updates_selected_in_place(tmp_path: Path) -> None:
    agent = _CandidateAgent()
    manager, managed = _manager(tmp_path, agent)
    checkpoints: list[int] = []

    def check_saved_proposals() -> None:
        snapshot = manager.load_snapshot(managed.agent_id)
        saved = deserialize_context_tree(snapshot["session"])
        leaves = saved.get_active_leaves()
        assert len(leaves) == 1
        assert leaves[0].step.model_output == "SELECTED"
        assert leaves[0].step.observations == []  # durable before side effects
        rejected = saved.get_siblings(leaves[0].node_id)[0]
        assert rejected.status is NodeStatus.REJECTED
        assert rejected.step.error is not None
        assert rejected.step.usage == {"output_tokens": 11}
        assert leaves[0].score == 0.8
        checkpoints.append(len(saved.nodes))

    agent.before_execution = check_saved_proposals
    events = list(manager.stream(managed, "first"))
    assert all(not isinstance(event, ActionEvent) or event.event_type != "candidates" for event in events)
    assert checkpoints == [3]
    assert len(managed.context.nodes) == 3  # task + 2 proposals; no duplicate executed node
    leaf = managed.context.get_active_leaves()[0]
    assert leaf.step is agent.selected_steps[0]
    assert leaf.step.observations == ["executed:1"]
    assert leaf.generation_params == {"breadth": 2, "candidate_index": 1}
    assert agent.executions == 1

    # A second request extends the selected leaf, including after persistence.
    assert manager.run(managed, "second") == "answer:2"
    assert checkpoints == [3, 6]
    restored = deserialize_context_tree(manager.load_snapshot(managed.agent_id)["session"])
    assert [step.task for step in restored.steps if isinstance(step, TaskStep)] == ["first", "second"]
    assert len(restored.nodes) == 6
    assert "REJECTED" not in str(restored.to_messages())
    assert len(restored.active_leaf_ids) == 1


@pytest.mark.parametrize("recoverable", [True, False])
def test_selected_candidate_errors_update_existing_node(tmp_path: Path, recoverable: bool) -> None:
    error = ModelOutputProtocolError("invalid selected proposal") if recoverable else RuntimeError("executor failed")
    agent = _CandidateAgent(error=error)
    manager, managed = _manager(tmp_path, agent)
    manager.run(managed, "task")
    selected = [node for node in managed.context.nodes.values() if node.annotations.get("selected")]
    assert len(selected) == (2 if recoverable else 1)
    assert selected[0].step is agent.selected_steps[0]
    assert str(selected[0].step.error) == str(error)
    assert selected[0].step.model_output == "SELECTED"
    assert selected[0].status is (NodeStatus.ACTIVE if recoverable else NodeStatus.FAILED)
    assert agent.executions == (1 if recoverable else 0)
    assert len(managed.context.nodes) == (5 if recoverable else 3)


def test_manager_reset_clears_historical_paths_and_candidates(tmp_path: Path) -> None:
    agent = _CandidateAgent()
    manager, managed = _manager(tmp_path, agent)
    manager.run(managed, "old task")
    old_path = managed.context.create_path_from_node(managed.context.active_leaf_ids[0])
    managed.context.all_paths.append(old_path)
    managed.context.selected_path_id = old_path.path_id
    manager.run(managed, "fresh task", reset_session=True)
    assert managed.context.selected_path_id is None
    assert managed.context.all_paths == []
    assert len(managed.context.nodes) == 3
    assert managed.context.stats["total_nodes_created"] == 3
    assert "old task" not in str(managed.context.to_messages())


def _tree_with_candidates() -> ContextTree:
    tree = ContextTree(tree_id="audit", system_prompt="system")
    tree.append_step(TaskStep(task="first task"))
    for number in range(1, 4):
        parent = tree.active_leaf_ids[0]
        for selected in (False, True):
            node = ContextNode(
                node_id=f"{'selected' if selected else 'rejected'}_{number}",
                step=ActionStep(step_num=number, model_output=f"{'GOOD' if selected else 'BAD'}_{number}",
                                observations=[f"observation_{number}"], usage={"output_tokens": number}),
                status=NodeStatus.ACTIVE if selected else NodeStatus.REJECTED,
                score=float(number),
            )
            tree.add_node(node, parent_id=parent)
            if selected:
                tree.active_leaf_ids = [node.node_id]
    tree.append_step(TaskStep(task="latest task"))
    return tree


class _SummaryModel:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def generate(self, messages):
        self.inputs.append(str(messages))
        return {"content": "<summary>completed earlier work</summary>"}


def test_remain_preserves_rejected_proposals_and_only_projects_selected_observations() -> None:
    tree = _tree_with_candidates()
    projected = RemainCompressStrategy(k=1, placeholder="omitted").compress(tree)
    assert projected.nodes.keys() == tree.nodes.keys()
    assert projected.nodes["selected_1"].step.observations == ["omitted"]
    assert projected.nodes["selected_3"].step.observations == ["observation_3"]
    assert projected.nodes["rejected_1"].step.observations == ["observation_1"]
    assert projected.nodes["rejected_1"].step.usage == {"output_tokens": 1}
    assert tree.nodes["selected_1"].step.observations == ["observation_1"]
    assert "BAD_" not in str(projected.to_messages())


@pytest.mark.parametrize("incremental", [True, False])
def test_compact_preserves_candidate_audit_and_follows_selected_branch_after_append(incremental: bool) -> None:
    tree = _tree_with_candidates()
    original_ids = set(tree.nodes)
    model = _SummaryModel()
    strategy = CompactCompressStrategy(step_threshold=1, compression_model=model, incremental=incremental)
    compacted = strategy.compress(tree)
    assert original_ids.issubset(compacted.nodes)
    assert isinstance(compacted.steps[0], SummaryStep)
    assert [step.step_num for step in compacted.steps if isinstance(step, ActionStep)] == [3]
    assert isinstance(compacted.steps[-1], TaskStep)
    assert compacted.steps[-1].task == "latest task"
    assert "GOOD_1" in model.inputs[0] and "BAD_" not in model.inputs[0]
    assert "GOOD_1" not in str(compacted.to_messages())
    for number in range(1, 4):
        assert compacted.nodes[f"rejected_{number}"].status is NodeStatus.REJECTED

    compacted.append_step(ActionStep(step_num=4, model_output="NEW_SELECTED", observations=["new result"]))
    assert compacted.steps[-1].model_output == "NEW_SELECTED"
    assert compacted.get_context_messages(compacted.active_leaf_ids[0]) == compacted.to_messages()
    assert "BAD_" not in str(compacted.to_messages())
    assert len(compacted.active_leaf_ids) == 1
    restored = deserialize_context_tree(serialize_context_tree(compacted))
    assert restored.to_messages() == compacted.to_messages()

    # A second compression uses the live suffix, not a frozen completed path.
    again = strategy.compress(restored)
    assert [step.step_num for step in again.steps if isinstance(step, ActionStep)] == [4]
    assert "latest task" in model.inputs[-1]
    assert "BAD_" not in str(again.to_messages())
    assert original_ids.issubset(again.nodes)


def test_default_context_follows_active_leaf_over_a_high_score_historical_path() -> None:
    tree = _tree_with_candidates()
    old_path = tree.create_path_from_node("selected_1")
    old_path.success = True
    old_path.total_score = 100
    tree.all_paths.append(old_path)
    tree.selected_path_id = old_path.path_id
    assert isinstance(tree.steps[-1], TaskStep)
    assert tree.steps[-1].task == "latest task"
    assert tree.to_linear_steps(path_id=old_path.path_id)[-1].model_output == "GOOD_1"
