# Runner

`Runner` 是单个对话请求的统一入口。它不保存 Agent、Tool、Graph 或后台任务的 live
实例；这些可变对象分别由 Manager 持有。静态声明只由 Registry 解析与 fresh 构造。

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

## 开发约束

- `RunnerConfig` 是可序列化数据；内置及自定义 mode 共用 `stream()/run()/stop()`，不注册动态 factory、callback 或独立循环。Runner 可保存请求锁、取消状态和监听器，不能持有活 Agent/Tool/Graph/Task。
- mode 切换先验证目标，且只在 root、后台任务和 Graph 都空闲时应用；保留 Runner ID 与快照，忙碌时拒绝切换。未完成 Team 工作阻止切换。旧 manifest schema 明确报错，不自动迁移。
- Team 任务状态先保存再发事件；成员轮次由共享 `AsyncTaskManager` 调度，session 由 `AgentManager` checkpoint；收件消息 ID 在接收 step 保存后确认。请求流在 `round_end` 前等待活跃成员调用并送出 `team_update`。
- `AgentManager.acquire()` 依次加载快照、Registry fresh 构造、恢复 session、绑定 context、checkpoint。`stop()` 协作取消，`close()` 释放资源；终态与恢复日志包含 Runner、Agent/任务/Graph ID 和原因。持久化只含数据，不序列化活 Python 对象。
- 边界、生命周期与布局测试放 `tests/core/runner/`，并覆盖内置 mode 共用执行链、并发/取消/恢复及旧 schema 拒绝。
