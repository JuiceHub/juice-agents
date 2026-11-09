> [English](README.md)

# 本地 Web 工作台

`./juice-web` 提供浏览器中的会话、任务和文件预览界面。
先按[项目 README](../../README.zh-CN.md)安装 SDK、网关和前端依赖，然后运行：

```bash
conda activate juice-agents
./juice-web
```

打开终端打印的 `http://127.0.0.1:5173/` 地址。
脚本启动本机 Web 网关与 Vite 开发服务器。网关只接受本机连接，目前没有远程访问认证。

初始化使用后端 bootstrap 的 `effective_config`（基础 → `~/.juice/config.yaml` → 项目
`.juice/config.yaml`），`workspace_config` 保持仅项目覆盖语义。`GET /api/effective-config`
提供相同的只读合并视图；用户修改仍通过 `PUT /api/workspace-config` 保存稀疏项目补丁。
启动选择不自动保存继承默认值。凭据复用进程环境变量 → 项目 `.env` → `~/.juice/.env`。

## 界面与能力

| 区域 | 作用 |
| --- | --- |
| 左侧 | 选择项目、会话和子 Agent；搜索历史、查看 Skills |
| 中央 | 阅读对话、工具结果与审批请求；运行时继续输入，消息进入持久化 inbox |
| 右侧 | 操作当前 Runner 的 Browser，或只读预览工作区文件 |

Team 会话显示任务完成数、成员状态和错误。输入框支持 `/agents`、`/teams`、`/skills` 等命令、`$<skill>` / `$workflow:<name> <任务>` 显式调用，以及模型与 thinking effort 选择。

网关 client 通过共享协议类型提供独立生图目录 `listImageModels` 和稀疏更新
`setImageConfig({ model_name?, enabled? })`；选择模型保留生图开关及聊天/奖励模型。
此版本 Web 不增加生图选择面板，可使用 CLI `/image_model` 或工作区
`image.model_name` 配置，生图工具另行通过配置开启。

`/clear` 立即创建并切换到空白 session；没有活动 session 时也会新建。新 session 沿用当前模型、effort、执行模式、Agent 类型、权限、策略和奖励模型。运行中按 FIFO 等当前 round 结束再执行，后续输入进入新 session。在子 Agent 视图中也可使用，创建成功后返回 root 对话；创建失败保留可见记录，并按网关实际状态校准活动 session。新会话保存在 `.juice/sessions/<runner_id>/`；通过 `/resume <runner_id>` 可恢复之前的会话。

`/agents [name]` 和 `/teams [name]` 通过 Registry 查询，不创建 Runner 或资源文件；展示有效的 `local` / `builtin` 来源，内置 Team 的 `manifest_path` 为 `null`。本地声明优先，首次成功编辑才保存覆盖，删除覆盖后恢复内置。`/resources` 及其专用网关 API 已从分发、帮助和补全中移除。

Browser 使用 Playwright 的真实 Chromium 页面。首次使用前执行 `python -m playwright install chromium`；打开独立可见窗口需要桌面会话，纯 SSH 环境可使用内嵌预览。

## 代码入口与边界

| 位置 | 职责 |
| --- | --- |
| `src/App.tsx` | 布局、启动数据和流状态组装 |
| `src/gateway/client.ts` | HTTP/WebSocket 请求与事件路由 |
| `src/components/ThreadView.tsx` | root 与子 Agent 的消息及输入 |
| `src/components/PreviewPanel.tsx` | Browser / Files 预览切换 |
| `src/lib/` | 命令、补全和布局辅助逻辑 |
| `../shared/` | 共用协议、会话状态与展示模型 |

### 开发约定

CodeAct 历史使用持久化 Python wrapper 的工具状态与详情，返回结果和捕获异常均
放在可展开的工具条目中；已有 wrapper 时，不再根据 `code_action` 另建 Python 条目。

- Web 的 runner 即任务会话；子 Agent 归属当前 runner。子 Agent 输入和停止分别使用 `send_agent_message`、`interrupt_agent`；root 使用自身的流请求与取消请求。
- 公开事件统一由 Runner 常驻订阅接收；`stream_message` 仅等待并返回 `RoundResult`。shared 通过 `round_start` / `round_end` 跟踪运行状态，按 `event_id` 去重，取消定向到 `{runner_id, round_id}`。切换会话解除旧 Runner 订阅并拒绝迟到事件。连接关闭或出错时拒绝待完成请求并显示错误；消息队列仍可继续使用。
- 消息生命周期、stream 和 command 的展示模型复用 shared 包。Web 组件只负责样式；模型输出的 Markdown 不启用原始 HTML，用户输入和工具参数按纯文本显示。
- `/teams` 等只读查询不创建 Runner。模型和 effort 选择由网关持久化到工作区配置；`/agent-type` 只改变以后新建 Agent 的默认值，不修改当前会话。
- `/clear` 仅在 `start_session` 成功后清空界面；复用 `src/lib/session.ts` 的创建流程与输入 FIFO，在转发子 Agent 输入前识别 `/clear`。策略和奖励模型由网关在同工作区 runtime 替换时保留。
- Browser 操作当前 Runner 的 Playwright session；真实页面通过网关提供的实时画面和交互接口显示，不使用 iframe 或虚拟标签页。文件读取必须经过网关的工作区路径检查。
- 网关仅用于本地访问。若增加远程访问能力，需先设计身份认证与访问控制。

## 验证

在仓库根目录运行：

```bash
conda activate juice-agents
cd frontend
npm test --workspace juice-web
npm run typecheck --workspace juice-web
npm run build --workspace juice-web
npx tsx --test ../tests/frontend/test_web_clear.ts
```

涉及网关或 Browser 行为时，还需运行相关的 `tests/adapters/web_gateway/` 测试。

公开事件使用带类型约束的 `kind + data`，包含 `event_id`、数值 `timestamp`、Runner/Agent 身份及适用的回合/步骤身份。Agent 状态为 `idle/running/released`，回合结果为 `completed/failed/cancelled`。shared reducer 统一维护去重、root/child 对话隔离、工具原位更新与按调用 ID 归并的工作流进度；CLI/Web 共用同一 stream presenter。

新建/恢复遵循 `bindRunner → describe_agent_sessions → applySnapshot → activate_session`；查询可传 `agent_id` 按需读取单个 Agent 历史。取消期间显示“正在停止”，直到真实提交的 `round_end`；取消拒绝、请求失败和断线均不伪造结束。重连后通过 `describe_agent_sessions` 快照校正状态。旧快照格式明确拒绝，原文件保留。

后端 `message_received` 事件使异步完成回执在主 Agent 回复前显示。Actor 页面展示
初始任务和用户/Team 消息；会话附件与快照的 `pending_messages` 在重连后恢复消息。
调度、投递和消费确认均由后端完成，Web 通过 shared presenter 展示后端事件和快照。
