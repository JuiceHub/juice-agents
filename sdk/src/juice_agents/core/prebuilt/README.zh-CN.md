> [English](README.md)

# Prebuilt 工厂

`prebuilt.factory` 是静态组合辅助工具。它补齐缺失的内置 Agent 声明，选择声明式 `RunnerConfig`，然后调用 `Runner.create()` 或 `Runner.resume()`。

```text
内置声明初始化 → RunnerConfig → Runner → Managers
```

它不会构造 root Agent、启动 session、注入协调器或改变某种 mode 的执行流程。root 仍由 `AgentManager.acquire_root()` 按需创建。

`build_prebuilt_agent_declarations()` 会将相同的默认声明以脱离状态的对象形式返回，供冷查询 `/agents` 使用，不会写文件。只有 Runner 创建时的 `seed_prebuilt_declarations()` 才会持久化缺失的 YAML。

```python
from juice_agents.core.prebuilt import create_prebuilt_runner

runner = create_prebuilt_runner(runner_config="team", base_dir=".")
```

workspace 已有的声明优先于默认值；初始化只补缺失项。

## 开发约定

- `build_prebuilt_agent_declarations()` 只返回内存中的默认声明；仅 `seed_prebuilt_declarations()` 在 Runner 创建边界写入缺失 YAML。
- 工厂只接收 `RunnerConfig` 与 binding 声明，不接收 live Agent 或协调器回调；创建后 `AgentManager.live_agents` 应仍为空。
- mode 切换在 Runner 空闲时应用不可变目标配置；恢复时使用当前 schema 的 `Runner.resume()`。
