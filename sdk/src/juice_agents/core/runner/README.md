# Runner

> [简体中文](README.zh-CN.md)

`Runner` is the unified entry point for one conversation request. It does not retain live Agent, Tool, Graph, or background-task instances; Managers own those mutable objects. Registry resolves and freshly constructs static declarations.

> [Chinese](README.zh-CN.md)

```text
Agent / Tool / Graph declarations
              │ resolve · validate · instantiate
              ▼
           Registry
              │ fresh objects only
              ▼
Runner ── owns ──► AgentManager      session · snapshot · stream · cancel · release
  │               ToolManager        runtime binding · permissions · audit
  │               GraphRunManager    graph run · checkpoint · recovery
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

The runtime root is always the in-memory `AgentConfig(name="root")`; no mode
resolves a root YAML. Mode changes retain the Runner ID and root session
snapshot, then refresh the effective root configuration at an idle boundary.
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

## Lifecycle and persistence

`AgentManager.acquire()` is the only initialization path: it loads or creates
an Agent snapshot, asks `AgentRegistry.instantiate()` for a fresh object,
restores its session, binds the runtime context, then checkpoints it.

- `persistent` Agents retain their live instance and session until released.
- `functional` Agents are checkpointed and released after their request.
- `stop()` cooperatively cancels Managers; `close()` releases their resources.
- `resume()` accepts only the current Runner manifest schema. Historical
  `.juice/runners/` data is not migrated or resumed.

Runner state is rooted at `.juice/runners/<runner_id>/`. The manifest stores
the serialized `RunnerConfig`, selected Team and mode-local disabled Agents;
Agent runtime snapshots live under `agents/`. The formal plan artifact is
always `plans/root.md`.
Managers own their corresponding task, graph and tool runtime records.

## Module map

| Path | Responsibility |
| --- | --- |
| `runner.py` | creation/resume, request dispatch, Manager composition |
| `config.py` | `RunnerConfig`, `AgentBinding`, capability and policy templates |
| `execution/context.py` | narrow Agent-to-Runner capability bridge |
| `persistence/store.py` | Runner manifest and directory layout |
| `core/agent/manager.py` | managed Agent lifecycle and snapshots |
| `core/managers/` | Tool, GraphRun and AsyncTask runtime ownership |

When changing this boundary, add architectural tests that prove Registry has no
live state, Manager owns lifecycle, and Runner does not directly instantiate or
execute domain objects.

## Development conventions

- `RunnerConfig` is serializable data. Built-in and custom modes share `stream()`, `run()`, and `stop()`; do not register a dynamic factory, callback, or separate loop. Runner may keep request locks, cancellation state, and listeners, but never live Agent, Tool, Graph, or Task objects.
- Validate a mode target before applying it, and change modes only when root, background tasks, and Graphs are idle. Preserve the Runner ID and snapshot; reject a busy change and any unfinished Team work. Reject old manifest schemas explicitly instead of migrating them.
- Persist Team task state before emitting events. Schedule member turns through the shared `AsyncTaskManager`, checkpoint sessions through `AgentManager`, and acknowledge inbox IDs only after the receiving step is saved. The request stream waits for active member calls and emits `team_update` before `round_end`.
- `AgentManager.acquire()` loads a snapshot, constructs a fresh Registry object, restores its session, binds context, and checkpoints. `stop()` cancels cooperatively; `close()` releases resources. Terminal and recovery logs include Runner, Agent/task/Graph IDs and the reason. Persistence contains data only, never live Python objects.
- Put boundary, lifecycle, and layout tests in `tests/core/runner/`, covering the shared execution chain, concurrency, cancellation, recovery, and old-schema rejection across built-in modes.
