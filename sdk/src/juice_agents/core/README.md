# Core runtime

The core runtime follows a strict ownership chain:

```text
Registry (static) → Manager (live state) ← Runner (request dispatch)
```

`agent/` contains Agent protocol/session code, `registry/` contains static
declarations, `managers/` owns live Tool/Graph/Task services, and `runner/`
composes them for a request. `group/` and `team/` contain declaration support;
they do not add a separate execution runtime.

Core never depends on `adapters/`. Protocol adapters, frontends and
examples consume the SDK from outside this boundary.

## Module documentation

- Execution: [Agent](agent/README.md), [Runner](runner/README.md),
  [Managers](managers/README.md), [Permissions](permissions/README.md).
- Declarations: [Registry](registry/README.md), [Prebuilt](prebuilt/README.md),
  [Group](group/README.md), [Team](team/README.md).
- Services: [Models](models/README.md), [Memory](memory/README.md),
  [Graph](graph/README.md), [Cron](cron/README.md),
  [Exporter](exporter/README.md).

各模块的 README 同时说明用途、公开用法与必要的开发约束。

## 开发边界

- 静态声明放在 Registry，运行中可变状态由 Manager 持有，请求编排放在 Runner。
- `group/` 和 `team/` 只提供声明与共享能力；运行逻辑归 `RunnerConfig`、Runner 和 Managers。
- Core 不导入协议适配器，也不恢复旧的 live registry、invoker、mode callback 或并行 executor API。
- 改动模块边界时在 `tests/core/` 对应模块补充功能测试，并更新该模块 README。
