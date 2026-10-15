> [English](README.md)

# juice-agents

轻量级 Python 智能体框架，支持 **会话恢复**、**工作流编排** 和 **多智能体协作**，构建 ReAct 和 CodeAct 智能体。

**当前版本：** `0.1.0`（开发版）

## 核心功能

| 功能 | 说明 |
| --- | --- |
| 🔄 **会话恢复** | 进程重启后恢复智能体、工作流和任务 |
| 🎯 **多种智能体模式** | `agent`、`plan`、`group`、`team` — 一个运行时，四种协作模式 |
| 🌐 **工作流编排** | 分支、并行、循环和检查点支持复杂工作流 |
| 🛠️ **丰富工具集** | Shell、Python、文件、Web、浏览器、图像、MCP 及自定义工具 |
| 💾 **持久化状态** | 会话、工作流、后台任务和团队看板在重启后保留 |
| 🚀 **多种接口** | Python SDK、终端 CLI 和本地 Web 工作台 |

```text
用户请求 → Runner → Agent / Workflow / Team → 工具
              │                            │
              └──── 持久化、审批、审计 ─────┘
```

## 快速开始

### 安装

需要 Conda。在仓库根目录执行：

```bash
conda env create -f environment.yml
conda activate juice-agents
python -m pip install -e ./sdk
python -m pip install -r adapters/requirements.txt
npm ci --prefix frontend
```

配置在线模型的 API 凭证：

```bash
mkdir -p .juice
cp sdk/src/juice_agents/_assets/config.example.yaml .juice/config.yaml
cp .env.example .env
# 在 .env 中填写 API 密钥
```

浏览器工具需要：`python -m playwright install chromium`

### 第一个程序

```python
from juice_agents import Juice

juice = Juice(workspace=".")
runner = juice.runners.create(runner_config="agent", permission_mode="default")

# 流式获取执行事件
for event in runner.stream("分析这个项目的模块边界"):
    print(event)

# 稍后恢复同一会话（即使进程重启后）
runner = juice.runners.resume(runner_id=runner.runner_id)
print(runner.run("把建议整理成实施顺序"))
```

### 智能体模式

| 模式 | 适用场景 |
| --- | --- |
| `agent` | 单智能体执行，可选委派 |
| `plan` | 先调研和规划，审批后执行 |
| `group` | 根智能体协调专职智能体 |
| `team` | 持久任务板，成员认领和消息协作 |

### CLI 和 Web

```bash
./juice       # 终端界面
./juice-web   # 本地 Web 工作台（仅本机访问）
```

## 架构

```text
Registry（静态配置） → Manager（运行时状态） ← Runner（执行调度）
```

- **Registry**：解析、验证和实例化对象
- **Manager**：管理生命周期、持久化和取消
- **Runner**：组合管理器并调度请求
- **Adapter**：映射 stdio/HTTP/WebSocket 协议

所有智能体模式通过 Runner 共享同一执行路径。状态持久化到工作区的 `.juice/`。

## 开发

运行测试：

```bash
conda activate juice-agents
python -m pytest tests -q
cd frontend && npm test --workspaces --if-present
```

贡献代码应包含测试、文档更新和验证结果。开发指南见 [AGENTS.md](AGENTS.md)。

## 模块文档

- [SDK](sdk/README.zh-CN.md) · [适配层](adapters/README.zh-CN.md) · [示例](examples/README.zh-CN.md)
- [Core](sdk/src/juice_agents/core/README.zh-CN.md) · [Workflow](sdk/src/juice_agents/core/workflow/README.zh-CN.md)
- [CLI](frontend/cli/README.zh-CN.md) · [Web](frontend/web/README.zh-CN.md) · [前端共享模块](frontend/shared/README.zh-CN.md)

## 许可证

[Apache License 2.0](LICENSE)
