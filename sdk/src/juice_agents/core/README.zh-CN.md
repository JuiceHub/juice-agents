> [English](README.md)

# Core 运行时

Core 运行时遵循明确的所有权链：

```text
Registry（静态声明）→ Manager（运行状态）← Runner（请求调度）
```

`agent/` 包含 Agent 协议与会话代码，`registry/` 管理静态声明，`managers/` 持有运行中的 Tool、Graph 和 Task 服务，`runner/` 为请求组合这些服务。`group/` 和 `team/` 提供声明支持，不实现独立执行时序。

Core 不依赖 `adapters/`。协议适配器、前端和示例都从此边界之外调用 SDK。

## 模块文档

- 执行： [Agent](agent/README.zh-CN.md)、[Runner](runner/README.zh-CN.md)、[Managers](managers/README.zh-CN.md)、[Permissions](permissions/README.zh-CN.md)
- 声明： [Registry](registry/README.zh-CN.md)、[Prebuilt](prebuilt/README.zh-CN.md)、[Group](group/README.zh-CN.md)、[Team](team/README.zh-CN.md)
- 服务： [Models](models/README.zh-CN.md)、[Memory](memory/README.zh-CN.md)、[Graph](graph/README.zh-CN.md)、[Cron](cron/README.zh-CN.md)、[Exporter](exporter/README.zh-CN.md)

各模块 README 说明模块用途、公开用法和必要的开发约定。

## 开发边界

- 静态声明放在 Registry，运行中的可变状态由 Manager 持有，请求编排由 Runner 负责。
- `group/` 和 `team/` 只提供声明与共享能力；运行逻辑归 `RunnerConfig`、Runner 和 Managers。
- Core 不导入协议适配器，也不恢复旧版 live registry、invoker、mode callback 或并行 executor API。
- 调整模块边界时，在 `tests/core/` 对应模块添加行为测试，并更新该模块 README。
