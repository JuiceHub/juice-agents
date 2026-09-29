> [English](README.md)

# juice-agents

`juice-agents` 是一个 Python 智能体框架：用同一套运行时构建 ReAct / CodeAct Agent、
调用工具、编排 Graph，并在进程重启后恢复会话和任务。
当前为 `0.1.0` 开发版；持久化状态只支持当前 schema，升级前请备份工作区的 `.juice/`。

## 能做什么

| 能力 | 用途 |
| --- | --- |
| Agent 与工具 | 使用 Shell、Python、文件、Web、浏览器、图像、MCP 等工具处理任务 |
| Graph | 通过分支、并行、循环和 checkpoint 编排多步流程 |
| 多 Agent | 用 `agent`、`plan`、`group`、`team` 四种内置配置运行任务 |
| 持久化 | 恢复会话、Graph、后台任务和 Team 任务板 |
| 多种入口 | 直接调用 Python SDK，或使用终端 CLI 和本地 Web 工作台 |

```text
用户请求 → Runner → Agent / Graph / Team → 工具
              │                         │
              └── 会话、任务、审批与审计 ──┘
```

## 从源码安装

需要 Conda。以下命令在仓库根目录运行；环境文件会安装 Python 和 Node.js。

```bash
conda env create -f environment.yml
conda activate juice-agents
python -m pip install -e ./sdk
python -m pip install -r adapters/requirements.txt
npm ci --prefix frontend
```

使用在线模型前，复制配置模板并在 `.env` 或 shell 环境中填写所用服务的密钥：

```bash
mkdir -p .juice
cp sdk/src/juice_agents/_assets/config.example.yaml .juice/config.yaml
cp .env.example .env
```

`.env` 和 `.juice/` 不应提交。浏览器工具需要额外执行
`python -m playwright install chromium`。

## 第一个 SDK 程序

```python
from juice_agents import Juice

juice = Juice(workspace=".")
runner = juice.runners.create(runner_config="agent", permission_mode="default")

for event in runner.stream("分析这个项目的模块边界"):
    print(event)

# runner_id 可用于在之后的进程中恢复同一会话。
runner = juice.runners.resume(runner_id=runner.runner_id)
print(runner.run("把建议整理成实施顺序"))
```

四种内置配置共享 Runner 执行链路：

| 配置 | 适合的任务 |
| --- | --- |
| `agent` | 单个 Agent 直接执行，可按需委派 |
| `plan` | 先调查和制定计划，再经审批执行 |
| `group` | 由 root 调度一组专职 Agent |
| `team` | 持久任务板、成员认领和消息协作 |

Team 由 root 创建或选择，再添加成员；已有成员定义可以在新的任务板中复用。
更多配置与运行时规则见 [Runner](sdk/src/juice_agents/core/runner/README.zh-CN.md)
和 [Team](sdk/src/juice_agents/core/team/README.zh-CN.md)。

## CLI 与 Web

```bash
./juice       # 终端界面
./juice-web   # 本地网页工作台
```

Web 网关仅供本机使用，未提供远程访问认证，入口拒绝非本机监听地址。
Python SDK 可以独立构建；CLI、Web 和适配层目前通过仓库源码运行，
尚未作为独立安装包发布。

## 开发约定

```text
Registry（静态声明） → Manager（运行时对象与状态） ← Runner（请求调度）
```

| 边界 | 约定 |
| --- | --- |
| Registry | 解析、校验并新建对象；不保存活动会话或执行状态 |
| Manager | 管理 Agent、工具、Graph、后台任务的生命周期、持久化与取消 |
| Runner | 组合 Manager 并调度请求；各运行配置共用同一执行路径 |
| Adapter | 只映射 stdio / HTTP / WebSocket 协议；SDK 核心不导入 `adapters/` |

Agent 通过 Runner 提供的执行上下文调用工具，使权限、取消和审计作用于同一请求。
`RunnerConfig` 是运行配置的数据契约；自定义配置不能注入另一套执行循环。
Team 的任务、成员消息和状态由 Manager 持久化，只有 root 可以创建任务和修改任务资格。
Plan 的审批只由当前 Runner 的 root 发起；旧版 Runner manifest 不会自动迁移。

运行状态写在调用方工作区的 `.juice/`；新增写入需保持原子性，并对取消、恢复和
失败记录可关联 Runner ID 的日志。实现变更应复用现有接口，在 `tests/` 添加针对
行为的测试，并更新受影响模块的 README。

## 测试与参与

```bash
conda activate juice-agents
python -m pytest tests -q
cd frontend && npm test --workspaces --if-present
```

提交改动时请附上相关测试、文档更新和验证结果。
一般问题和功能建议可使用 GitHub Issues；安全问题请使用仓库的私密漏洞报告入口，
不要发布公开 Issue。

项目按 [Apache License 2.0](LICENSE) 发布。参与贡献前请确认提交内容
可按该许可证分发；第三方依赖仍遵循各自的许可证。

发布前在干净环境执行 Python 与前端测试、类型检查和构建，检查 SDK wheel 的文件
与元数据。公开仓库应从审查过的源码快照建立，不携带私有 Git 历史；再启用 CI、
密钥扫描、依赖告警和私密漏洞报告。SDK wheel 不包含 CLI 和 Web。

## 模块说明

- [SDK](sdk/README.zh-CN.md) · [适配层](adapters/README.zh-CN.md) · [示例](examples/README.zh-CN.md)
- [Core](sdk/src/juice_agents/core/README.zh-CN.md) · [Graph](sdk/src/juice_agents/core/graph/README.zh-CN.md)
- [CLI](frontend/cli/README.zh-CN.md) · [Web](frontend/web/README.zh-CN.md) · [前端共享模块](frontend/shared/README.zh-CN.md)
