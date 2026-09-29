# Core runtime

> [简体中文](README.zh-CN.md)

The core runtime follows a strict ownership chain:

```text
Registry (static) → Manager (live state) ← Runner (request dispatch)
```

`agent/` contains Agent protocol and session code, `registry/` contains static declarations, `managers/` owns live Tool/Graph/Task services, and `runner/` composes them for a request. `group/` and `team/` contain declaration support; they do not add a separate execution runtime.

Core never depends on `adapters/`. Protocol adapters, frontends, and examples consume the SDK from outside this boundary.

## Module documentation

- Execution: [Agent](agent/README.md), [Runner](runner/README.md), [Managers](managers/README.md), [Permissions](permissions/README.md).
- Declarations: [Registry](registry/README.md), [Prebuilt](prebuilt/README.md), [Group](group/README.md), [Team](team/README.md).
- Services: [Models](models/README.md), [Memory](memory/README.md), [Graph](graph/README.md), [Cron](cron/README.md), [Exporter](exporter/README.md).

Each module README documents its purpose, public usage, and necessary development conventions.

## Development boundaries

- Keep static declarations in Registry, mutable runtime state in Managers, and request orchestration in Runner.
- `group/` and `team/` provide declarations and shared capabilities only; runtime behavior belongs to `RunnerConfig`, Runner, and Managers.
- Core does not import protocol adapters or restore old live registry, invoker, mode callback, or parallel executor APIs.
- Add behavior tests under the corresponding `tests/core/` module when changing a module boundary, and update that module's README.
