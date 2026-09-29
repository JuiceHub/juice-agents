# 接入层（adapters）

`adapters/` 将 `juice_agents` 的 Runner 接入本地 CLI 和 Web 界面，不随 SDK wheel 发布。执行与状态管理由 SDK 负责；接入层只处理协议、事件序列化、交互审批和当前 Runner 引用。

| 模块 | 用途 | 入口 |
| --- | --- | --- |
| `stdio_gateway/` | CLI 使用的 stdin/stdout JSON-RPC 服务 | `python -m adapters.stdio_gateway.entry` |
| `web_gateway/` | 本地 HTTP/WebSocket 服务 | `./juice-web` 或 `python -m adapters.web_gateway.entry` |

在仓库根目录安装 SDK 和 Web 依赖后启动：

```bash
conda activate juice-agents
python -m pip install -e ./sdk
python -m pip install -r adapters/requirements.txt
python -m adapters.web_gateway.entry
```

Web 服务默认监听 `127.0.0.1:8003`。它提供会话和模型状态、工作区内的只读文件预览、Runner 事件流与审批交互，以及当前 Runner 的浏览器预览和控制。stdio 服务提供相应的 Runner 会话、模型、配置、Skill/Plugin/Team、worktree 与流式消息 RPC；CLI 通常负责启动它。

## 开发约定

```text
CLI / Web ── JSON-RPC / HTTP / WebSocket ──> adapters ──> juice_agents Runner
```

- 依赖只能从 `adapters` 指向 `juice_agents`。新增协议入口放在 `adapters/` 的独立子目录；不要在接入层复制 Runner、Agent、Graph 或配置存储逻辑。
- stdio 的 stdout 只承载 JSON-RPC；日志写 stderr。stdio 和 WebSocket 共用事件序列化，原样透传 Runner 的 Team 任务状态、session 标题预览及模型能力信息。
- 工作区配置只读写 `.juice/config.yaml`，通过 SDK 的配置接口持久化。冷查询 Plugin、Skill 和 Team 配置时不创建 Runner；仅在已有 Runner 上重装配变更。
- Web 服务没有身份认证，仅允许监听 loopback 地址；HTTP/WebSocket 的 Host 与浏览器 WebSocket 的 Origin 均须限制为本机。文件预览只能读取工作区内路径，符号链接目录不能展开到工作区外。
- 浏览器控制复用当前 Runner 的 Playwright session。Playwright 对象只能由所属线程操作；`/ws/browser/live` 负责导航和输入，`/api/browser/live/new-tab` 创建真实 tab。
- 流式传输期间仍须接收 `answer_ask` 和 `cancel_stream`。取消审批等待时要返回取消结果，错误的 `request_id` 要报告错误并继续等待正确答复。

框架安装和整体使用方式见[项目 README](../README.md)。
