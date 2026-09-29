# Permissions

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

## 开发约束

- `policy.py` 集中处理允许、询问、拒绝。只读边界和 Agent 目标限制来自可序列化的 `RunnerConfig.ToolPolicy`，不按 mode 名称分支。
- 共享配置写入验证 `AgentManager` 管理的调用者身份；一次性批准属于当前请求状态。
- 在 `Tool.forward()` 前测试策略拒绝，并测试旧的已解析 action 无法绕过新组合的策略上下文。
