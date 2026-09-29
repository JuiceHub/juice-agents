> [English](README.md)

# 终端界面（CLI）

`./juice` 在终端中运行 Agent，支持会话恢复、工具审批、任务委派和 Git worktree。
安装与启动步骤见[项目 README](../../README.zh-CN.md)。输入 `/help` 可查看当前版本的完整命令。

## 常用操作

| 操作 | 作用 |
| --- | --- |
| `Enter` / `Alt+Enter` | 提交输入 / 在输入框内换行；运行中的新输入按顺序排队 |
| `PgUp` / `PgDn` | 浏览当前会话记录 |
| `Esc` / `Ctrl+C` | 中断当前回合 |
| `Ctrl+S` / `Ctrl+T` | 查看子 Agent / 后台任务 |
| `/resume` | 恢复已有会话 |
| `/mode` / `/permissions` | 切换运行模式 / 审批策略 |
| `/model` / `/config` | 选择模型 / 编辑工作区配置 |
| `/agents` / `/teams` | 查看可用 Agent / Team |
| `/worktree` | 查看或切换当前 Git worktree |

输入 `/` 可补全命令及参数。`./juice --worktree [name]` 会在独立 worktree 中启动任务；
退出时默认保留未合并的改动。

## 代码入口与边界

| 位置 | 职责 |
| --- | --- |
| `src/entry.tsx`、`src/app.tsx` | 启动、会话状态、界面组装 |
| `src/gateway/client.ts` | stdio 网关通信与请求路由 |
| `src/components/` | 输入框、选择器和任务面板 |
| `src/ink-ext/` | 备用屏和行级滚动；详见[模块 README](src/ink-ext/README.zh-CN.md) |
| `src/lib/` | 命令调度、文本布局和展示模型 |
| `../shared/` | CLI/Web 共用的协议、会话状态和展示逻辑 |

### 开发约定

- 会话消息生命周期复用 `@juice-agents/shared/conversation`；网关协议和 stream/command 展示逻辑复用 shared 包。CLI 只管理终端交互。
- 所有 transcript 和输入区域由 Ink 在备用屏中渲染。消息先转换成视觉行，再用 `VirtualScrollList` 截取可见行；总高度由 `computeOverlayBudget` 控制，避免触发 Ink 的清屏路径。不要向 stdout 另写一套消息渲染。
- 选择器、审批和任务面板使用独占输入焦点。面板打开时隐藏输入框；`ask_request` 只通过 `answer_ask` 回复，接受成功后再关闭提问。
- 普通 `Enter` 提交输入，只有 `Alt/Meta+Enter` 换行。正在运行时普通消息按 FIFO 排队；取消会清除当前提问和等待状态，不应阻塞后续消息。
- `/mode` 改变当前 Runner 的 Agent 模式，`/permissions` 改变审批策略；`/agent-type` 只修改以后创建的默认 Agent。工作区配置写入 `.juice/config.yaml` 的 `runtime` 节。
- `--worktree` 和 `/worktree` 通过网关操作 worktree，不在 CLI 中直接调用 Git。会话和子 Agent 的输入、取消分别使用对应的网关请求。
- 新的视觉状态使用 `src/components/design-system/` 的主题 token 和组件；`JUICE_NO_ANIMATION=1` 时动画组件提供静态显示。

## 验证

在仓库根目录运行：

```bash
conda activate juice-agents
cd frontend
npm test --workspace juice-cli
npm run typecheck --workspace juice-cli
```

涉及网关请求或会话语义时，还需运行相关的 `tests/adapters/stdio_gateway/` 测试。
