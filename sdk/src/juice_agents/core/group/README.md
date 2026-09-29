# Group declarations

> [简体中文](README.zh-CN.md)

`core/group/` contains static built-in Agent declarations used by the declarative `group` `RunnerConfig`. It does not contain a group-specific runtime loop, task store, or live worker collection.

```text
group builtin config ──> AgentRegistry ──> AgentManager
                                         └── Runner capability dispatch
```

The `group` configuration differs from other built-ins only through root/member bindings, collaboration capability, and concurrency policy. All requests still flow through the shared `Runner.stream()` path.

## Development conventions

This module contains built-in prompts, roles, and `AgentConfig` declarations only. Member lifecycle belongs to `AgentManager`, background work belongs to `AsyncTaskManager`, and shared capabilities are implemented through `RunnerConfig` and Managers. Do not add a separate execution branch or persistence directory keyed on `mode == "group"`.
