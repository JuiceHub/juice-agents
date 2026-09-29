# Prebuilt factory

> [简体中文](README.zh-CN.md)

`prebuilt.factory` is a static composition helper. It seeds missing built-in
Agent declarations, selects a declarative `RunnerConfig`, then calls
`Runner.create()` or `Runner.resume()`.

```text
builtin declaration seed → RunnerConfig → Runner → Managers
```

It never constructs a root Agent, starts a session, injects a coordinator or
changes a mode's execution flow. Root acquisition remains lazy in
`AgentManager.acquire_root()`.

`build_prebuilt_agent_declarations()` returns those same defaults detached in
memory for cold `/agents` display. It never writes; only
`seed_prebuilt_declarations()` persists missing YAML during Runner creation.

```python
from juice_agents.core.prebuilt import create_prebuilt_runner

runner = create_prebuilt_runner(runner_config="team", base_dir=".")
```

Existing workspace declarations win over built-in defaults; seeding is
missing-only.

## Development conventions

- `build_prebuilt_agent_declarations()` returns detached in-memory defaults only; only `seed_prebuilt_declarations()` writes missing YAML at the Runner creation boundary.
- The factory accepts `RunnerConfig` and binding declarations only. It does not accept live Agents or coordinator callbacks; `AgentManager.live_agents` remains empty after creation.
- Apply mode changes at an idle Runner boundary using an immutable target configuration, and recover with the current-schema `Runner.resume()`.
