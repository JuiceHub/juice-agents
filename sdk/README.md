# Juice Agents SDK

`sdk/` 是可独立构建和安装的 Python SDK。包名为 `juice-agents`，导入名为
`juice_agents`，需要 Python 3.11 或更新版本。它提供 Agent、Graph、Team 和可恢复的
Runner。CLI、Web 前端位于 `frontend/`，协议适配器位于 `adapters/`；这些都不属于
SDK 安装包。

## 安装

```bash
conda activate juice-agents
python -m pip install -e ./sdk
```

以上命令从仓库根目录运行。构建本地发行包时运行
`python -m build --sdist --wheel --outdir sdk/dist sdk`，随后可安装
`sdk/dist/` 中的 wheel。使用托管模型前，在 workspace 的 `.juice/config.yaml`
配置模型，并通过环境变量提供 API 密钥；模板位于
`sdk/src/juice_agents/_assets/config.example.yaml`。

## 使用

```python
from juice_agents import Juice

juice = Juice(workspace=".")
runner = juice.runners.create(
    permission_mode="default",
    runner_config="agent",
)

print(runner.run("先分析需求"))
for event in runner.stream("继续实现"):
    print(event)

runner = juice.runners.resume(runner_id=runner.runner_id)
runner.run("继续原对话")

# UI 进程持有常驻时钟；SDK 提供原子的 due/tick 原语。
juice.cron.tick()
```

`run()` 消费 `stream()` 并返回最终结果；两者都向当前 Runner Session 追加内容。
新对话必须创建新 Runner。同一 Runner 同时只处理一个用户请求。
当前请求启动的有限后台 Agent、Graph 或命令由 Runner 等待并自动注入一次通知；
Team 成员按任务或消息执行有限回合，空闲时不占用线程；Team 请求流等待当前成员
调用结束，以便传递完整的 `team_update` 进度。Cron 不阻塞普通请求。

## 发布边界

wheel 只包含 `juice_agents`、类型标记和公开内置资源，不包含 `adapters/`、示例、
`.env`、真实 `config.yaml` 或 `.juice` 运行数据。配置、Runner 状态和缓存写入调用方
workspace。

运行时模块及其边界见 [Core 文档](src/juice_agents/core/README.md)。

## 开发与发布约定

| 路径 | 职责 |
| --- | --- |
| `pyproject.toml` | 包元数据、依赖和构建配置 |
| `src/juice_agents/__init__.py`、`_client.py` | 稳定导出与 `Juice` 门面 |
| `src/juice_agents/core/` | Agent、Graph、Runner、存储等实现及各模块 README |
| `src/juice_agents/_assets/` | 用 `importlib.resources` 加载的模板与内置 Skill |

- `Runner.stream()` 是请求执行入口，`run()` 只消费其事件；同一 Runner 请求互斥，`run/stream` 均追加当前会话。新对话通过 `runners.create()` 建立。
- Runner 负责 `$skill/$plugin/$graph`、Goal/Plan 续跑和本次请求关联的后台任务；适配器不能用空消息模拟续跑。
- 终态任务先持久化通知，再按事件 ID 投递；恢复时只重放未消费通知。
- SDK 不导入仓库级 `adapters`；运行数据只写入调用方 workspace 的 `.juice/`，资源通过 `importlib.resources` 读取。

在仓库根目录验证：

```bash
conda activate juice-agents
python -m pip install -e ./sdk
python -m pytest tests/sdk -q
```

构建前确认许可证、版本和源码历史；从干净的源码树构建并检查 sdist、wheel 中的说明与资源。`sdk/build/`、`sdk/dist/` 和 `sdk/src/*.egg-info/` 仅是本地构建产物。

SDK 使用 [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)。构建产物应包含许可证文本及 `Apache-2.0` 元数据。
