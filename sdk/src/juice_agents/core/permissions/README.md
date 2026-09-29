# Permissions

> [简体中文](README.zh-CN.md)

Permissions combine approval choice with the current RunnerConfig policy:

| Source | Controls |
| --- | --- |
| `permission_mode` | whether an approval-required action asks (`default`) or proceeds (`accept`) |
| `RunnerConfig.tool_policy` | declared tool allow-list, read-only boundary, workflow exceptions and Agent target allow-list |

`ToolManager` composes the RunnerConfig policy before Agent-specific
permissions. A stale resolved tool action cannot reuse an earlier permissive
context. `PermissionEngine` then applies workspace rules and asks the user as
needed.

```text
RunnerConfig policy → ToolManager → PermissionEngine → Tool.forward()
```

The managed Agent identity comes from
`RunnerContext.agent_id → AgentManager`; editable declarations and tool
constructor arguments are never authorization proof.

## Development conventions

- `policy.py` centralizes allow, ask, and deny decisions. Read-only boundaries and Agent-target restrictions come from serializable `RunnerConfig.ToolPolicy`; do not branch on mode names.
- Validate caller identity managed by `AgentManager` before writing shared configuration; one-time approvals belong to the current request state.
- Test policy rejection before `Tool.forward()` and verify that an action resolved under an older policy cannot bypass a newly composed policy context.
