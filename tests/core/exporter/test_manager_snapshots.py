"""Exporter regression tests for the Manager-owned runtime layout."""

from __future__ import annotations

import json

from juice_agents.core.agent.manager import JsonAgentSnapshotStore
from juice_agents.core.agent.sessions import ActionStep, AgentSession, TaskStep, serialize_session
from juice_agents.core.exporter import TrajectoryExporter
from juice_agents.core.runner.config import mode_registry
from juice_agents.core.runner.persistence import blank_runner_state, build_layout, write_runner_state


def test_exporter_reads_agent_manager_snapshot(tmp_path) -> None:
    """The exporter must consume Manager snapshots, never a removed actor layout."""

    runner_id = "export-manager-snapshot"
    layout = build_layout(tmp_path, runner_id)
    layout.ensure_dirs()
    runner_state = blank_runner_state(
        runner_id=runner_id,
        permission_mode="default",
        agent_mode="agent",
        root_agent_name="general",
    )
    runner_state["runner_config"] = mode_registry.resolve("agent").to_dict()
    write_runner_state(layout.manifest_path, runner_state)

    session = AgentSession(system_prompt="system instruction")
    session.append_step(TaskStep(task="summarize the architecture"))
    session.append_step(
        ActionStep(
            step_num=1,
            model_output="done",
            round_outcome="submitted",
            output="done",
        )
    )
    JsonAgentSnapshotStore(layout.agents_dir).save(
        {
            "schema_version": 1,
            "agent_id": "root",
            "agent_name": "general",
            "role": "root",
            "lifecycle": "persistent",
            "status": "idle",
            "is_root": True,
            "metadata": {},
            "session": serialize_session(session),
        }
    )

    output_path = tmp_path / "trajectory.jsonl"
    result = TrajectoryExporter(tmp_path).export_runner(runner_id, output_path)

    assert result.num_samples == 1
    sample = json.loads(output_path.read_text(encoding="utf-8"))
    assert sample["metadata"] == {
        "runner_id": runner_id,
        "agent_id": "root",
        "agent_name": "general",
        "agent_role": "root",
        "agent_mode": "agent",
        "mode_id": "agent",
        "is_root": True,
        "created_at": runner_state["created_at"],
    }
