# Group declarations

`core/group/` contains static built-in Agent declarations used by the
declarative `group` `RunnerConfig`. It does not contain a group-specific
runtime loop, task store or live worker collection.

```text
group builtin config ──> AgentRegistry ──> AgentManager
                                         └─ Runner capability dispatch
```

The `group` configuration differs from other built-ins only through root/member
bindings, collaboration capability and concurrency policy. All requests still
flow through the same `Runner.stream()` path.

## 开发约束

此模块只存放内置提示词、角色和 `AgentConfig` 声明。成员生命周期归 `AgentManager`，后台工作归 `AsyncTaskManager`；共享能力通过 `RunnerConfig` 和 Managers 实现，避免按 `mode == "group"` 建立独立执行分支或持久化目录。
