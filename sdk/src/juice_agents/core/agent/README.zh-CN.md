> [English](README.md)

# Agent

`core/agent` 定义 Agent 协议，并由 `AgentManager` 管理运行生命周期。

```text
AgentBinding → AgentRegistry.instantiate() → AgentManager.acquire()
                                              → ManagedAgent
                                                 session / cancel / checkpoint
```

- `agents.py`：ReAct 与 CodeAct 单步协议。Agent 在 Manager 绑定前是全新且不持有运行状态的对象。
- `manager.py`：live Agent、会话快照、执行锁、中断和释放的唯一所有者。
- `attachments.py`：会话附件协议，包括 `agent_user_message` 和异步任务通知。
- `tools/`：Tool 声明和适配器，不维护 Agent 自己的工具池。

内置 Skill 文件位于 `juice_agents/_assets/skills/`，通过包资源加载，不放在 `core/agent/` 下。

Agent 只能通过受限的 `RunnerContext` 执行工具：

```text
Agent action → RunnerContext.execute_tools()
             → Runner.tool_manager.execute_for_agent()
             → ToolManager policy / permission / schedule / audit
```

这样可以避免 Agent 持有 worker pool、权限上下文或第二套任务运行时。`AgentManager` 先从 `AgentRegistry` 获取全新实例，再恢复会话。功能型 Agent 在请求结束后释放；持久型 Agent 由 Manager 持有。声明变更只会在之后的新一次 `AgentManager.acquire()` 中生效，不会原地重建 live Agent。

Plan Mode 是运行时 root Agent 的一种能力，不存在保留名称为 `plan` 的子 Agent 声明。默认辅助 Agent 为 `general` 和 `explore`；workspace 可通过 Registry 注册其他名称。

公开入口使用 `Runner.create(runner_config=...)` 或 SDK 客户端。不支持向 `Runner.create()` 传入已创建的 root Agent。

## 开发约定

- `AgentManager.acquire()` 依次读取快照、通过 Registry 新建 Agent、恢复 session、绑定 `RunnerContext` 并保存 checkpoint。持久型 Agent 留在 Manager 中；功能型 Agent 在请求结束后释放。
- Agent 不持有 ToolManager、线程池、Graph 实例或持久化目录，也没有未绑定时的备用工具执行路径。ReAct 和 CodeAct 的 action 都经 `RunnerContext` 交给 `ToolManager`，统一执行权限、取消和审计。
- Registry 声明编辑只对下次 acquire 生效，不热替换运行中的 Agent。Plan 是 root 的能力，不是特殊托管 Agent。
- 内置 Skill 保存在 `_assets/skills/`。生命周期测试放在 `tests/core/agent/`，工具路由测试放在 `tests/core/managers/`。
