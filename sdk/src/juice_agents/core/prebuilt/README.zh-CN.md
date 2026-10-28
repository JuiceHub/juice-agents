> [English](README.md)

# Prebuilt 工厂

`prebuilt.factory` 选择声明式 `RunnerConfig`，然后调用 `Runner.create()` 或 `Runner.resume()`。内置 Agent 声明和 default Team 在首次成功编辑前保持在内存中。

```text
RunnerConfig → Runner → Registry（本地覆盖 → 内置定义）→ Managers
```

它不会构造 root Agent、启动 session、注入协调器或改变某种 mode 的执行流程。root 仍由 `AgentManager.acquire_root()` 按需创建。

`build_prebuilt_agent_declarations()` 委托 AgentRegistry 的统一目录返回脱离状态的默认声明。冷查询、构造与运行均不保存 Agent YAML 或 Team manifest；Runner 运行记录仍按原有规则保存。

```python
from juice_agents.core.prebuilt import create_prebuilt_runner

runner = create_prebuilt_runner(runner_config="team", base_dir=".")
```

本地声明优先于内置定义；首次成功编辑保存完整本地覆盖，删除覆盖即恢复内置定义。纯内置资源不可删除，seed API 已移除。

## 开发约定

- 内置目录统一放在 Registry；预置声明构建函数只委托该目录读取，不写资源文件。
- 工厂只接收 `RunnerConfig` 与 binding 声明，不接收 live Agent 或协调器回调；创建后 `AgentManager.live_agents` 应仍为空。
- mode 切换在 Runner 空闲时应用不可变目标配置；恢复时使用当前 schema 的 `Runner.resume()`。
