# Prebuilt factory

> [简体中文](README.zh-CN.md)

`prebuilt.factory` selects a declarative `RunnerConfig`, then calls
`Runner.create()` or `Runner.resume()`. Built-in Agent declarations and the
default Team remain in memory until the first successful edit.

```text
RunnerConfig → Runner → Registry (local override → builtin) → Managers
```

It never constructs a root Agent, starts a session, injects a coordinator or
changes a mode's execution flow. Root acquisition remains lazy in
`AgentManager.acquire_root()`.

`build_prebuilt_agent_declarations()` projects detached defaults from the
unified AgentRegistry catalog. Cold queries, construction, and execution do
not save Agent YAML or Team manifests. Runner runtime records keep their
existing persistence behavior.

```python
from juice_agents.core.prebuilt import create_prebuilt_runner

runner = create_prebuilt_runner(runner_config="team", base_dir=".")
```

Local declarations win over built-in defaults. A successful edit saves a full
local override; deleting it restores the built-in definition. Built-in
definitions themselves cannot be deleted. The seed API has been removed.

## Development conventions

- Keep the built-in catalog in Registry; the pure prebuilt helper delegates to it and does not write resources.
- The factory accepts `RunnerConfig` and binding declarations only. It does not accept live Agents or coordinator callbacks; `AgentManager.live_agents` remains empty after creation.
- Apply mode changes at an idle Runner boundary using an immutable target configuration, and recover with the current-schema `Runner.resume()`.
