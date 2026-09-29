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

## 界面与能力

| 区域 | 作用 |
| --- | --- |
| 左侧 | 选择项目、会话和子 Agent；搜索历史、查看 Skills |
| 中央 | 阅读对话、工具结果与审批请求；运行时继续输入，消息按序排队 |
| 右侧 | 操作当前 Runner 的 Browser，或只读预览工作区文件 |

Team 会话显示任务完成数、成员状态和错误。输入框支持 `/agents`、`/teams`、`/skills`、`/deep-research` 等命令，以及模型与 thinking effort 选择。

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

- Web 的 runner 即任务会话；子 Agent 归属当前 runner。子 Agent 输入和停止分别使用 `send_actor_message`、`interrupt_actor`；root 使用自身的流请求与取消请求。
- WebSocket 流事件按请求 `id` 路由。连接关闭或出错时拒绝待完成请求并显示错误；消息队列仍可继续使用。
- 消息生命周期、stream 和 command 的展示模型复用 shared 包。Web 组件只负责样式；模型输出的 Markdown 不启用原始 HTML，用户输入和工具参数按纯文本显示。
- `/teams` 等只读查询不创建 Runner。模型和 effort 选择由网关持久化到工作区配置；`/agent-type` 只改变以后新建 Agent 的默认值，不修改当前会话。
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
```

涉及网关或 Browser 行为时，还需运行相关的 `tests/adapters/web_gateway/` 测试。
