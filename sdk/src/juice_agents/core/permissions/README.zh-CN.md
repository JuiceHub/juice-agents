> [English](README.md)

# Permissions

权限系统把审批选择与当前 `RunnerConfig` 策略组合起来：

| 来源 | 控制内容 |
| --- | --- |
| `permission_mode` | 需要审批的操作是询问用户（`default`）还是直接继续（`accept`） |
| `RunnerConfig.tool_policy` | 声明的工具白名单、只读边界、工作流例外及 Agent 目标白名单 |

`ToolManager` 先组合 `RunnerConfig` 策略，再应用 Agent 级权限。先前按宽松上下文解析的旧 action 不能复用旧权限。随后 `PermissionEngine` 应用 workspace 规则，并在需要时向用户请求审批。

```text
RunnerConfig policy → ToolManager → PermissionEngine → Tool.forward()
```

受管理 Agent 的身份来自 `RunnerContext.agent_id → AgentManager`。可编辑的声明和工具构造参数都不能作为授权凭据。

## 开发约定

- `policy.py` 集中处理允许、询问和拒绝。只读边界与 Agent 目标限制来自可序列化的 `RunnerConfig.ToolPolicy`，不按 mode 名称分支。
- 写入共享配置前验证调用者是 `AgentManager` 管理的 Agent；一次性批准只属于当前请求状态。
- 在 `Tool.forward()` 前测试策略拒绝，并测试旧解析 action 无法绕过新组合的策略上下文。
