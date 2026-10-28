> [English](README.md)

# 终端界面（CLI）

`./juice` 在终端中运行 Agent，支持会话恢复、工具审批、任务委派与任意项目工作区。
安装与启动步骤见[项目 README](../../README.zh-CN.md)。输入 `/help` 可查看当前版本的完整命令。

## 安装一次，从任意项目启动

按根 README 安装依赖后，绑定当前源码仓库及 Python、Node 解释器：

```bash
conda activate juice-agents
./install-juice                       # 默认 ~/.local/bin/juice
# 可选：./install-juice --bin-dir /your/bin
cd /path/to/project
juice
juice --workspace /path/to/other-project
```

将 `~/.local/bin` 加入 Shell 的 `PATH` 后，日常启动无需激活 Conda。启动器保留目标项目的
`PATH`，分别记录 Python 和 Node（包括 NVM 路径）；正常启动只检查依赖，不自动安装、
删除目录或修改锁文件。源码/解释器移动后重新运行安装器；前端依赖通过
`cd frontend && npm ci` 显式修复。

目录优先级：`--workspace` → `JUICE_WORKSPACE_DIR` → 启动目录（直接用 npm 时通过
`INIT_CWD` 保留启动位置）。不存在或非目录时立即报错。Gateway 在目标目录运行，
通过显式模块搜索路径加载框架。

CLI 初始化和 `/config` 读取后端 `load_effective_config`：SDK/显式基础配置 →
`~/.juice/config.yaml` → 项目 `.juice/config.yaml` → 本次启动选择。`/config` 和
`/model` 仅保存实际修改的项目字段。凭据优先级为进程环境变量 → 项目 `.env` →
`~/.juice/.env`；无密钥也能打开 CLI 和查看帮助。新建/恢复会话时重新加载用户与项目
AGENTS 规则，并加入 system prompt。

## 常用操作

| 操作 | 作用 |
| --- | --- |
| `Enter` / `Alt+Enter` | 提交输入 / 在输入框内换行；运行中的新输入按顺序排队 |
| `PgUp` / `PgDn` | 浏览当前会话记录 |
| `Esc` / `Ctrl+C` | 中断当前回合；空闲且输入为空时 `Ctrl+C` 退出 |
| `Shift` + 拖拽（iTerm2 用 `Option`） | 选中文本复制（绕过应用内鼠标滚动） |
| `Ctrl+S` / `Ctrl+T` | 查看子 Agent / 后台任务 |
| `/resume` | 恢复已有会话 |
| `/mode` / `/permissions` | 切换运行模式 / 审批策略 |
| `/model` / `/config` | 选择模型 / 编辑工作区配置 |
| `/rm [model_name\|reset]` | 打开奖励模型选择页、按名称设置或清除，复用 `/models` 目录 |
| `/strategy breadth=N` / `/strategy reset` | 配置每步候选数量 / 恢复线性执行 |
| `/agents` / `/teams` | 查看可用 Agent / Team |
| `/worktree` | 预留接口，提示如何使用已有 Git worktree |

输入 `/` 可补全命令及参数。内置 `--worktree` 和 `/worktree` 管理尚未实现；先用
`git worktree add` 创建目录，再执行 `juice --workspace <directory>`。

输入 `/str` 可找到 `/strategy`；输入 `/strategy ` 后，候选提供 `breadth=3` 和
`reset`，并显示 `<breadth=N|reset>` 内联提示。候选中的数量可以改为任意正整数；
参数输入完成后关闭候选列表。

奖励评分复用 `/models` 中的模型名称、连接配置和密钥解析：

```text
/models
/rm <model_name>
/strategy breadth=3
/rm
/rm reset
```

`/rm` 无参像 `/model` 一样打开选择页，复用同一模型目录，标记当前奖励模型，
并提供清除选项；设置或清除奖励模型不会改变 `breadth`，也不会切换生成模型。输入 `/rm ` 可补全目录模型名与 `reset`；不接受
额外参数或 effort 选项。

奖励模型设置仅在当前网关进程有效，重启 CLI 后需重新配置。`/strategy reset`
保留已选择的奖励模型；`/rm reset` 清除奖励模型但保留候选数量。

`breadth` 必须为正整数。配置奖励模型或通过 SDK 注入评分器后，每步在相同上下文
生成 N 个输出，评分后择一执行；奖励模型评分会增加模型请求，执行步数仍由
`max_steps` 控制。未配置评分器、`breadth=1` 和 `/strategy reset` 均线性执行。
首次消息前的设置在 root Agent 延迟创建时应用。不支持深度、保留路径数、温度
或逐层 breadth 列表。

`/agents [name]` 和 `/teams [name]` 展示 Registry 解析后的声明及 `local` / `builtin` 来源，查询不落盘。本地定义优先于内置；首次成功编辑才保存覆盖，删除覆盖后恢复内置。内置 Team 详情没有 manifest 路径。`/resources` 及其网关 API 已从命令、帮助和补全中移除。

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
- workspace 在入口统一解析并传递给 Gateway，不通过全局切换进程 cwd 实现。会话和子 Agent 的输入、取消分别使用对应网关请求。
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
策略与奖励模型命令功能测试集中在 `tests/frontend/`，已接入 CLI 的标准
测试命令；也可以从仓库根目录单独运行：

```bash
frontend/node_modules/.bin/tsx --test tests/frontend/test_strategy_command.ts tests/frontend/test_reward_model_command.ts tests/frontend/test_reward_model_selector.ts
```
