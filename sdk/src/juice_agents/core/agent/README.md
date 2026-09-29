# Agent

> [简体中文](README.zh-CN.md)

`core/agent` defines Agent protocols and owns their runtime lifecycle through `AgentManager`.

```text
AgentBinding -> AgentRegistry.instantiate() -> AgentManager.acquire()
                                             -> ManagedAgent
                                                session / cancel / checkpoint
```

- `agents.py`: ReAct and CodeAct step protocols. An Agent is a fresh, unowned step object until the Manager binds it.
- `manager.py`: the sole owner of live Agent instances, session snapshots, execution locks, interruption, and release.
- `attachments.py`: session attachment protocol, including `agent_user_message` and async-task notifications.
- `tools/`: Tool declarations and adapters. It contains no Agent-owned tool pool.

Built-in Skill files live in `juice_agents/_assets/skills/` and are loaded as package resources, not from `core/agent/`.

An Agent executes tools only through its narrow `RunnerContext`:

```text
Agent action -> RunnerContext.execute_tools()
             -> Runner.tool_manager.execute_for_agent()
             -> ToolManager policy / permission / schedule / audit
```

This prevents a fresh Agent from retaining a worker pool, permission context, or separate task runtime. `AgentManager` restores a session only after requesting a fresh instance from `AgentRegistry`. Functional Agents are released after their request; persistent Agents retain one Manager-owned instance. Declaration edits apply only at the next fresh `AgentManager.acquire()`; a live Agent is never reconstructed in place.

Plan Mode is a capability of the runtime root Agent. There is no reserved `plan` subagent declaration: `general` and `explore` are the default helpers, and a workspace can register any other Agent name through Registry.

Use `Runner.create(runner_config=...)` or the SDK client as the public entry point. Constructing a live root Agent for `Runner.create()` is unsupported.

## Development conventions

- `AgentManager.acquire()` follows snapshot load → fresh Agent from Registry → session restore → `RunnerContext` binding → checkpoint. Persistent Agents stay in the Manager; functional Agents are released after the request.
- Agents do not own a ToolManager, thread pool, Graph instance, or persistence directory, and have no fallback tool path while unbound. ReAct and CodeAct actions go through `RunnerContext` to `ToolManager` for shared permission, cancellation, and audit handling.
- Registry declaration edits apply on the next acquire; they do not hot-swap a running Agent. Plan is a root capability, not a special managed Agent.
- Built-in Skills live in `_assets/skills/`. Put lifecycle tests in `tests/core/agent/` and tool routing tests in `tests/core/managers/`.
