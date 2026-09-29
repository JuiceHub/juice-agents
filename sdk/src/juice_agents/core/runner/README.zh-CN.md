> [English](README.md)

# Runner

`Runner` 是单个对话请求的统一入口。它不持有 Agent、Tool、Graph 或后台任务的 live 实例；这些可变对象分别由 Manager 管理。静态声明只由 Registry 解析并构造为全新对象。

```text
Agent / Tool / Graph 声明
              │ resolve · validate · instantiate
              ▼
           Registry
              │ 仅返回全新对象
              ▼
Runner ── 持有 ──► AgentManager      session · snapshot · stream · cancel · release
  │                ToolManager        runtime binding · permissions · audit
  │                GraphRunManager    graph run · checkpoint · recovery
  └──────────────► AsyncTaskManager   task state · workers · notification · recovery
```

## 配置与运行模式

`RunnerConfig` 是不可变、可序列化的执行契约，声明：

- root 和成员使用的 `AgentBinding`；
- 能力开关、工具策略、并发上限和续跑策略。

内置的 `agent`、`plan`、`group`、`team` 共用同一条 `Runner.stream()` 路径，只在 binding、能力、并发和策略等数据上不同。`plan` 增加规划能力和只读工具策略。自定义 mode 通过 `RunnerConfig` 注册，不能安装改变执行流程的动态 factory 或 callback。

运行时 root 始终是内存中的 `AgentConfig(name="root")`，任何 mode 都不会解析 root YAML。mode 切换保留 Runner ID 和 root session snapshot，并在空闲边界刷新有效 root 配置。Plan Mode 由 root 管理：唯一正式计划文件是 `plans/root.md`，只有经 Manager 验证的 root 可以写入或申请审批。Team Mode 由 `TeamManager` 管理所选 Team 的任务板、消息、成员和完成状态。新 Team 不自动添加成员；再次选择已有 Team 时复用成员定义，但使用新的任务板。Runner 通过 `AsyncTaskManager` 调度资格符合且依赖已满足的成员轮次；`team_finish` 释放成员 session 前，`AgentManager` 持有这些 session。

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

`Runner.create()` 只接收声明。传入 live `MultiStepAgent` 或旧版 Runner schema 都会被拒绝。root 延迟创建，首个请求才调用 `AgentManager.acquire_root()`。

## Agent 与 Tool 调用路径

Agent 不拥有 executor 或 `ToolManager`。获取 Agent 时，`AgentManager` 会提供受限的 `RunnerContext`；每个 action 都经此上下文返回 Runner 所有的 ToolManager。

```text
MultiStepAgent
  └─ RunnerContext.execute_tools(...)
       └─ Runner.tool_manager.execute_for_agent(...)
            └─ Agent runtime binding、策略、取消与审计
```

这样可以复用 Agent，并避免工具调用绕过 Runner 的权限与取消边界。Manager 可以通过私有辅助类执行工作，但不能另行持有持久状态或暴露第二套 executor API。

## 生命周期与持久化

`AgentManager.acquire()` 是唯一初始化路径：读取或创建 Agent snapshot，通过 `AgentRegistry.instantiate()` 获取新对象，恢复 session，绑定运行时上下文，再保存 checkpoint。

- `persistent` Agent 保留 live 实例和 session，直到被释放。
- `functional` Agent 在请求结束后保存 checkpoint 并释放。
- `stop()` 协作式取消 Managers；`close()` 释放资源。
- `resume()` 只接受当前 Runner manifest schema。不会迁移或恢复旧 `.juice/runners/` 数据。

Runner 状态根目录为 `.juice/runners/<runner_id>/`。manifest 保存序列化后的 `RunnerConfig`、所选 Team 和 mode 内禁用的 Agent；Agent 运行快照位于 `agents/`。正式计划文件始终是 `plans/root.md`。对应的 Manager 持有任务、Graph 和工具运行记录。

## 模块索引

| 路径 | 职责 |
| --- | --- |
| `runner.py` | 创建/恢复、请求分发和 Manager 组合 |
| `config.py` | `RunnerConfig`、`AgentBinding`、能力和策略模板 |
| `execution/context.py` | Agent 到 Runner 的受限能力桥接 |
| `persistence/store.py` | Runner manifest 和目录布局 |
| `core/agent/manager.py` | Agent 生命周期与快照 |
| `core/managers/` | Tool、GraphRun 和 AsyncTask 的运行时所有权 |

修改此边界时，增加架构测试证明 Registry 不持有 live 状态、Manager 管理生命周期、Runner 不直接构造或执行领域对象。

## 开发约定

- `RunnerConfig` 是可序列化数据；内置和自定义 mode 共用 `stream()`、`run()`、`stop()`。不注册动态 factory、callback 或独立循环。Runner 可以持有请求锁、取消状态和监听器，但不能持有 live Agent/Tool/Graph/Task。
- 切换 mode 前先校验目标，只能在 root、后台任务和 Graph 都空闲时应用；保留 Runner ID 与 snapshot。忙碌时拒绝切换，未完成的 Team 工作也阻止切换。旧 manifest schema 明确报错，不自动迁移。
- Team 任务状态先持久化再发事件；成员轮次由共享 `AsyncTaskManager` 调度，session 由 `AgentManager` checkpoint；接收方 step 保存后才确认消息 ID。请求流需在 `round_end` 前等待活跃成员调用并发出 `team_update`。
- `AgentManager.acquire()` 依次加载 snapshot、通过 Registry 构造新对象、恢复 session、绑定 context 并保存 checkpoint。`stop()` 协作取消，`close()` 释放资源；终态与恢复日志包含 Runner、Agent/任务/Graph ID 和原因。持久化只含数据，不序列化 live Python 对象。
- 边界、生命周期和布局测试放在 `tests/core/runner/`，覆盖内置 mode 共用执行链、并发、取消、恢复及拒绝旧 schema。
