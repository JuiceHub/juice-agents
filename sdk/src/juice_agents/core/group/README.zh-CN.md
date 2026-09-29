> [English](README.md)

# Group 声明

`core/group/` 包含声明式 `group` `RunnerConfig` 使用的内置静态 Agent 声明。这里不包含专属执行循环、任务存储或 live worker 集合。

```text
group 内置配置 ──> AgentRegistry ──> AgentManager
                                      └── Runner 能力分发
```

`group` 配置只通过 root/member binding、协作能力和并发策略与其他内置配置区分。所有请求仍走统一的 `Runner.stream()` 路径。

## 开发约定

本模块只存放内置提示词、角色和 `AgentConfig` 声明。成员生命周期归 `AgentManager`，后台工作归 `AsyncTaskManager`；共享能力由 `RunnerConfig` 和 Managers 实现。不要按 `mode == "group"` 建立独立执行分支或持久化目录。
