# Agent

`core/agent` defines Agent protocols and owns their runtime lifecycle through
`AgentManager`.

```text
AgentBinding -> AgentRegistry.instantiate() -> AgentManager.acquire()
                                             -> ManagedAgent
                                                session / cancel / checkpoint
```

- `agents.py`: ReAct and CodeAct step protocols. An Agent is a fresh,
  unowned step object until the manager binds it.
- `manager.py`: the sole owner of live Agent instances, session snapshots,
  execution locks, interruption and release.
- `attachments.py`: the session attachment protocol, including
  `agent_user_message` and async-task notifications.
- `tools/`: Tool declarations and adapters. It contains no Agent-owned tool
  pool.

Built-in Skill files live in `juice_agents/_assets/skills/` and are loaded as
package resources; they are not stored under `core/agent/`.

An Agent executes a tool only through its narrow `RunnerContext`:

```text
Agent action -> RunnerContext.execute_tools()
             -> Runner.tool_manager.execute_for_agent()
             -> ToolManager policy / permission / schedule / audit
```

This prevents a fresh Agent from retaining a worker pool, permission context,
or a second task runtime. `AgentManager` restores a session only after it has
asked `AgentRegistry` for a fresh instance; functional Agents are released
after their request and persistent Agents retain one manager-owned instance.
Declaration edits are intentionally applied only on a subsequent fresh
`AgentManager.acquire()`; a live Agent is never reconstructed in place.

Plan Mode is a capability of the runtime root Agent. There is no reserved
`plan` subagent declaration: `general` and `explore` are the default helpers,
while a workspace may register any additional Agent name through the Registry.

Use `Runner.create(runner_config=...)` or the SDK client as the public entry
point. Constructing a live root Agent for `Runner.create()` is not supported.

## 开发约束

- `AgentManager.acquire()` 按“读取快照 → Registry 新建 Agent → 恢复 session → 绑定 `RunnerContext` → checkpoint”的顺序执行。持久 Agent 留在 Manager 中；功能型 Agent 在请求结束后释放。
- Agent 不持有 ToolManager、线程池、Graph 实例或持久化目录，也没有未绑定时的备用工具执行路径。ReAct 和 CodeAct 的动作都经 `RunnerContext` 交给 `ToolManager` 统一执行权限、取消和审计。
- Registry 声明编辑只对下次 acquire 生效，不热替换正在运行的 Agent。Plan 是 root 的能力，不是特殊的托管 Agent。
- 内置 Skill 保存在 `_assets/skills/`；生命周期测试放 `tests/core/agent/`，工具路由测试放 `tests/core/managers/`。
