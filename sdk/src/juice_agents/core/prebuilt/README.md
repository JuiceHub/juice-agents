# Prebuilt factory

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

## 开发约束

- `build_prebuilt_agent_declarations()` 只返回内存中的默认声明；仅 `seed_prebuilt_declarations()` 在 Runner 创建边界写入缺失 YAML。
- 工厂只接受 `RunnerConfig` 与 binding 声明，不接收活 Agent 或协调器回调；创建后 `AgentManager.live_agents` 应仍为空。
- 模式切换在 Runner 空闲时应用不可变目标配置，恢复使用当前 schema 的 `Runner.resume()`。
