# Runner

> [简体中文](README.zh-CN.md)

`Runner` is the unified entry point for one conversation request. It does not retain live Agent, Tool, Workflow, or background-task instances; Managers own those mutable objects. Registry resolves and freshly constructs static declarations.

```text
Agent / Tool / Workflow declarations
              │ resolve · validate · instantiate
              ▼
           Registry
              │ fresh objects only
              ▼
Runner ── owns ──► AgentManager      session · snapshot · stream · cancel · release
  │               ToolManager        runtime binding · permissions · audit
  │               WorkflowRunManager    workflow call · artifacts
  └──────────────► AsyncTaskManager  task state · workers · notification · recovery
```

## Configuration and modes

`RunnerConfig` is an immutable, serializable execution contract. It declares:

- root and member `AgentBinding`s;
- capability flags, tool policy, concurrency limit and continuation policy.

The built-in `agent`, `plan`, `group` and `team` configurations all use the
same `Runner.stream()` path. They differ only in data: bindings, capabilities,
concurrency and policies. `plan` adds planning capability and a read-only tool
policy. A custom mode is registered with a `RunnerConfig`; it cannot install a
dynamic factory or callback that changes execution flow.

Runner resolves `root` and members through AgentRegistry: local declarations
win, with built-in definitions as fallback. Creating or restoring a Runner
does not save default Agent YAML or the default Team manifest. The first
successful edit saves a full local override; deleting that override restores
the built-in definition. Plan and Team configuration is applied to an
in-memory copy; root remains excluded from
child-Agent discovery and dispatch. Mode changes retain the Runner ID and root
session snapshot, then refresh the effective configuration at an idle boundary.
Plan Mode is root-owned: `plans/root.md` is the only formal plan artifact and
only a Manager-verified root may write it or request approval. Team Mode uses
`TeamManager` for the selected Team's current task board, messages, members and
finish state. New Teams have no automatic members; selecting an existing Team
reuses its member definitions with a new board. The Runner schedules eligible,
dependency-ready member turns through `AsyncTaskManager`; member sessions stay
in `AgentManager` until `team_finish` releases them.

```python
from juice_agents.core.runner import AgentBinding, Runner, RunnerConfig

config = RunnerConfig(
    mode_id="review",
    root_binding=AgentBinding("root", "root", "persistent"),
    member_binding=AgentBinding("general", "reviewer", "functional"),
)
runner = Runner.create(base_dir=".", runner_config=config)
for event in runner.stream("Review this change"):
    handle(event)
```

`Runner.create()` accepts declarations only. Passing a live `MultiStepAgent`
or an old runner schema is rejected. Root creation is lazy: the first request
calls `AgentManager.acquire_root()`.

## Agent and tool call path

The workspace is `base_dir`, never the framework checkout or `.juice` itself.
`ConfigurationContext` supplies all project paths, merged configuration and
credentials; `runtime_config_path` selects the base layer and `runtime_overrides`
adds transient launch values. `reconfigure(runtime_overrides=...)` reacquires
Agents at an idle boundary while keeping sessions, Runner ID and the Team board.
Registry binds workspace defaults when tools are instantiated, including tools
added at runtime; portable Agent declarations never store injected machine paths.

```text
~/.juice/AGENTS[.override].md
             ↓
Git root → intermediate directories → base_dir
             ↓
root / child / Team / Workflow system prompts
```

`project_rules.py` prefers a nonempty override at each level and reads only
`base_dir` for non-Git projects. Combined UTF-8 text, including source/scope
labels, is limited to 32 KiB; omitted sources produce a warning. Custom role
prompts are retained. Create/resume takes a fresh rule snapshot, then replaces
the system prompt after transcript restoration. Agents read deeper directory
rules before modifying their files; no recursive scanning or tool interception
is performed. Linked Git worktrees use their own project root.

An Agent does not own an executor or a `ToolManager`. During acquisition,
`AgentManager` gives it a narrow `RunnerContext`; each action goes back through
that context to the Runner-owned ToolManager.

```text
MultiStepAgent
  └─ RunnerContext.execute_tools(...)
       └─ Runner.tool_manager.execute_for_agent(...)
            └─ Agent-specific runtime binding, policy, cancellation and audit
```

This keeps the Agent reusable and prevents a tool call from bypassing the
Runner's permission and cancellation boundary. Manager-private helpers may
execute work, but they do not own persisted state or expose a second executor
API.

`RunnerContext.list_available_agents()` uses the current Registry availability
projection for root and intersects it with explicit relationships for children.
`agent_tool` queries this view at invocation time; root's startup name snapshot
does not restrict newly saved Agents. Agent refreshes changed child names and
descriptions before its next model request. Listing, prompt and dispatch share
mode policy, Team membership and Runner/mode-local unload restrictions.

## Lifecycle and persistence

`AgentManager.acquire()` is the only initialization path: it loads or creates
an Agent snapshot, asks `AgentRegistry.instantiate()` for a fresh object,
restores its session, binds the runtime context, then checkpoints it.

- `persistent` Agents retain their live instance and session until released.
- `functional` Agents are checkpointed and released after their request.
- `stop()` cooperatively cancels Managers; `close()` releases their resources.
- `resume()` accepts only the current Runner manifest schema; historical Runner
  manifest schemas are not migrated or resumed.

Runner state is rooted at `.juice/runners/<runner_id>/`. The manifest stores
the serialized `RunnerConfig`, selected Team and mode-local disabled Agents;
Agent runtime snapshots live under `agents/`. The formal plan artifact is
always `plans/root.md`.
Managers own task and tool runtime records and workflow artifacts; workflow calls
do not have resumable run state.

## Token usage

```python
usage = runner.token_usage()
print(usage["generation"])
print(usage["reward"])
print(usage["total"])
records = runner.agent_manager.model_usage_records()
```

Each group contains:

| Field | Meaning |
| --- | --- |
| `requests` | Recorded observable SDK calls |
| `input_tokens`, `output_tokens`, `total_tokens` | Sums of reported, known counts |
| `missing_usage_requests` | Calls with missing or incomplete usage |

`Runner.token_usage()` delegates to `AgentManager`, the sole owner of usage
collection, persistence, and aggregation. Agent and strategy code explicitly
report model results or exceptions; records live in
`.juice/runners/<runner_id>/agents/model_usage.jsonl`, without prompts or
transcripts. Each record contains `kind`, `backend`, `model_name`, `agent_id`,
`agent_name`, `usage`, and `request_id`.

Use one active Manager per Runner; concurrent workers share that Manager.
Cold restoration reloads its usage records when the Manager is initialized.

Rejected candidates, empty-response retries, failed scoring, and usage known
before cancellation count; batch scoring counts once. Summary generation and
functional/ephemeral calls retain token metadata even without a saved session.
Compression and resume preserve the file. `ContextTree` does not own accounting;
`ActionStep.usage` is for tracing and window calibration and is not counted again.

Missing counts are unknown, not zero usage or a zero bill. Totals include known
counts alongside the missing-call count; they do not estimate tokens or costs.
SDK-internal transparent network retries and server usage that was never reported
cannot be reconstructed accurately. Direct model callers inspect the per-call
[`usage_calls` return field](../models/README.md#return-contract), also attached
to failures; Models do not maintain totals.

## Module map

| Path | Responsibility |
| --- | --- |
| `runner.py` | creation/resume, request dispatch, Manager composition |
| `config.py` | `RunnerConfig`, `AgentBinding`, capability and policy templates |
| `execution/context.py` | narrow Agent-to-Runner capability bridge |
| `persistence/store.py` | Runner manifest and directory layout |
| `core/agent/manager.py` | managed Agent lifecycle and snapshots |
| `core/managers/` | Tool, Workflow and AsyncTask runtime ownership |

When changing this boundary, add architectural tests that prove Registry has no
live state, Manager owns lifecycle, and Runner does not directly instantiate or
execute domain objects.

## Development conventions

- `RunnerConfig` is serializable data. Built-in and custom modes share `stream()`, `run()`, and `stop()`; do not register a dynamic factory, callback, or separate loop. Runner may keep request locks, cancellation state, and listeners, but never live Agent, Tool, Workflow, or Task objects.
- Validate a mode target before applying it, and change modes only when root, background tasks, and Workflows are idle. Preserve the Runner ID and snapshot; reject a busy change and any unfinished Team work. Reject old manifest schemas explicitly instead of migrating them.
- Persist Team task state before emitting events. Schedule member turns through the shared `AsyncTaskManager`, checkpoint sessions through `AgentManager`, and acknowledge inbox IDs only after the receiving step is saved. The request stream waits for active member calls and emits `team_update` before `round_end`.
- `AgentManager.acquire()` loads a snapshot, constructs a fresh Registry object, restores its session, binds context, and checkpoints. `stop()` cancels cooperatively; `close()` releases resources. Terminal and recovery logs include Runner, Agent/task/Workflow IDs and the reason. Persistence contains data only, never live Python objects.
- Put boundary, lifecycle, and layout tests in `tests/core/runner/`, covering the shared execution chain, concurrency, cancellation, recovery, and old-schema rejection across built-in modes.
