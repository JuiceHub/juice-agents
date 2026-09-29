# Trajectory Exporter

> [简体中文](README.zh-CN.md)

Export persisted `juice-agents` run trajectories as datasets that can be used for post-training.

## Capabilities

- **OpenAI Chat Completions format**: JSONL with strict user/assistant alternation, suitable for models using chat templates.
- **Three export scopes**: one Runner, multiple Runners, or a whole workspace.
- **Configurable retention**: `ExportConfig` controls which sessions and content are retained.
- **Streaming writes**: large datasets do not need to fit in memory.

## Data source

The exporter reads `.juice/runners/<runner_id>/agents/<agent_id>/session.json` in the workspace. `AgentManager` persists snapshots; the exporter reads them through `JsonAgentSnapshotStore` and session deserialization instead of reimplementing the disk format.

## Output format

Each line is a JSON object:

```json
{
  "messages": [
    {"role": "system", "content": "<system prompt>"},
    {"role": "user", "content": "<task>user task</task>"},
    {"role": "assistant", "content": "<thought>...</thought>\\n<actions>[...]</actions>"},
    {"role": "user", "content": "<observations>...</observations>"}
  ],
  "metadata": {
    "runner_id": "11a94b3a",
    "agent_id": "root",
    "agent_name": "general",
    "agent_role": "root",
    "mode_id": "agent",
    "is_root": true,
    "created_at": 1718436000.0
  }
}
```

| Source | Message |
| --- | --- |
| `AgentSession.system_prompt` | `{"role": "system"}` |
| `TaskStep` | `{"role": "user", "content": "<task>...</task>"}` |
| `ActionStep.model_output` | `{"role": "assistant"}` |
| `ActionStep.observations` | `{"role": "user", "content": "<observations>...</observations>"}` |
| `SummaryStep` | `{"role": "user"}` |

Adjacent messages with the same role are merged after conversion to preserve the strict user/assistant alternation required by post-training frameworks.

## Quick start

```python
from juice_agents.core.exporter import TrajectoryExporter

exporter = TrajectoryExporter(base_dir=".")
exporter.export_runner("11a94b3a", "out/r1.jsonl")
exporter.export_multiple_runners(["r1", "r2"], "out/batch.jsonl")
result = exporter.export_workspace("out/all.jsonl")
print(result.num_samples, result.num_messages)
```

## Configuration

`ExportConfig` or an equivalent dictionary controls session filters, content inclusion, and cleanup:

```python
from juice_agents.core.exporter import ExportConfig, TrajectoryExporter

config = ExportConfig(
    success_only=True,
    min_action_steps=2,
    max_action_steps=20,
    include_system_prompt=True,
    include_observations=True,
    include_reasoning=False,
    include_summary_steps=True,
    truncate_observations=8000,
    drop_error_steps=False,
)

TrajectoryExporter(".").export_workspace("out.jsonl", config=config)
TrajectoryExporter(".").export_workspace(
    "out.jsonl", config={"success_only": True, "min_action_steps": 2}
)
```

The default retains every session and all content. Callers can narrow it as needed.

## Return value

```python
result = exporter.export_workspace("out.jsonl", config={"success_only": True})

result.output_path           # output path
result.num_samples           # number of samples written
result.num_messages         # total messages across samples
result.num_sessions_scanned # sessions scanned, including filtered ones
result.num_sessions_skipped # sessions dropped by session-level filters
result.runner_ids           # Runner IDs that contributed samples
result.as_dict()             # dictionary form for printing or serialization
```

## Notes

- Image observations are represented by text placeholders such as `[image: <description>]`.
- Sessions with no `ActionStep`, or no conversation content after merging (system-only), are skipped.
- The output parent directory is created automatically. An empty export creates an empty file for consistent pipelines.
- **Privacy**: exported data is not automatically redacted. Review `system_prompt` and observations for credentials, absolute paths, and user data before training.

## Development conventions

| File | Responsibility |
| --- | --- |
| `filters.py` | Immutable `ExportConfig` and side-effect-free filtering |
| `formats.py` | Pure conversion from `AgentSession` to conversation samples |
| `exporters.py` | Snapshot reads, filtering, and streaming JSONL writes |

- Read Runner manifests and `JsonAgentSnapshotStore` snapshots through session deserialization. Do not reparse or copy older disk formats. Log a warning and skip damaged manifest/snapshot records.
- Preserve user/assistant alternation after conversion. Cover new step types in both input and output paths; the default filter retains all content. Images become text placeholders, and `tool_calls` remain in protocol text by default.
- Write one sample at a time; do not cache the dataset. Add formats in parallel in `formats.py` and dispatch them from `exporters.py`. Put tests in `tests/core/exporter/` and build fixtures through production persistence APIs.
