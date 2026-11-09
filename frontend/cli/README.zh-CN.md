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

如果启动目录已删除，启动器在调用 Python/Node 前报告工作目录不可用：先 `cd` 到
现存项目目录再重试。Python 预检仅在缺少模块时提示安装依赖，其他导入错误保留
原始 traceback 并报告启动失败。

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
| `Enter` / `Alt+Enter` | 提交输入 / 在输入框内换行；运行中的新输入进入持久化 inbox |
| `↑` / `↓` | 浏览共享输入历史 / 返回当前草稿 |
| `PgUp` / `PgDn` | 浏览当前会话记录 |
| `Esc` / `Ctrl+C` | 中断当前回合；空闲且输入为空时 `Ctrl+C` 退出 |
| `Shift` + 拖拽（iTerm2 用 `Option`） | 选中文本复制（绕过应用内鼠标滚动） |
| `Ctrl+S` / `Ctrl+T` | 查看子 Agent / 后台任务 |
| `/resume` | 恢复已有会话 |
| `/clear` | 沿用当前配置创建空白会话；旧会话仍可通过 `/resume` 恢复 |
| `/mode` / `/permissions` | 切换运行模式 / 审批策略 |
| `/model` / `/config` | 选择模型 / 编辑工作区配置 |
| `/rm [model_name\|reset]` | 打开奖励模型选择页、按名称设置或清除，复用 `/models` 目录 |
| `/image_model [model_name]` | 打开生图模型选择页，或按 `image_models` 中的名称选择；保留生图开关 |
| `/strategy breadth=N` / `/strategy reset` | 配置每步候选数量 / 恢复线性执行 |
| `/agents` / `/teams` | 查看可用 Agent / Team |
| `/worktree` | 预留接口，提示如何使用已有 Git worktree |

输入 `/` 可补全命令及参数。内置 `--worktree` 和 `/worktree` 管理尚未实现；先用
`git worktree add` 创建目录，再执行 `juice --workspace <directory>`。

每次工具调用只保留一条记录：`tool_start` 创建，`tool_end` 按 `call_id` 更新，
并发调用乱序结束也保持开始时的位置。参数仅在标题中展示首个非空值的首行，
最多 60 字符，省略内容或后续行用 `…` 标记；完整参数仍保留在原始事件和会话中。
普通结果按当前终端宽度折行后最多预览六行，下方标注省略的终端行数；浏览器返回的
单行 HTML 或序列化网页文本同样受限。明确失败和
捕获的异常在工具记录内完整展示；不根据返回文本判断失败。`submit_output` 的
最终回复只显示一次，CodeAct 提交也如此；事件内容和持久化 observation 仍完整保留。

```text
▸ Running shell (command: pytest)
              ↓ 相同 call_id
✗ Failed shell (command: pytest)
  ⎿ 工具结果 / 异常详情
```

会话保存在当前工作区的 `.juice/sessions/<runner_id>/`。`/clear` 会立即创建新会话，
首次输入前也可执行；保留当前模型、effort、执行模式、Agent 类型、审批策略、
候选策略及奖励模型。回合运行中按 FIFO 等待，后续输入随后进入新会话。
子 Agent 视图中也将其识别为全局命令，创建成功后返回主对话。创建失败时保留
可见记录，并根据网关实际状态校准活动会话，避免后续消息误发到已停止的 Runner。

`/resume` 列出已有会话及首条请求预览；`/resume <runner_id>` 切换整个 Runner，
主对话、子 Agent 历史、任务记录与会话状态一并恢复。子 Agent 视图中也可执行，
成功后返回主对话；回合运行中按 FIFO 等待，后续输入等待选择与加载完成。

CLI 输入（包括斜杠命令和多行提示词）保存在 `~/.juice/cli_history.jsonl`，由不同
工作区和 Runner 共享。提交时立即追加，开始浏览历史时重新读取，以获取其他进程的
新记录。该文件包含提交的文本，删除文件即可清空已保存的历史；它与 Bash 历史独立。

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

`/model` 和 `/rm` 共用 `models` 目录，`/image_model` 使用独立的 `image_models`；
两个目录通过 `providers` 复用连接和凭据环境变量配置。输入 `/image_model` 查看
当前生图模型与可用部署点，也可执行 `/image_model seedream_4`，选择保存到
`image.model_name`。生图工具默认关闭，在 `/config` 开启；选模型不会改变该开关、
聊天模型、奖励模型或策略，Plan 模式不挂载生图工具。打开或取消选择页不写配置；
运行中的选择命令按 FIFO 等待。输入 `/image_model ` 仅补全生图模型名。

`breadth` 必须为正整数。配置奖励模型或通过 SDK 注入评分器后，每步在相同上下文
生成 N 个输出，评分后择一执行；奖励模型评分会增加模型请求，执行步数仍由
`max_steps` 控制。未配置评分器、`breadth=1` 和 `/strategy reset` 均线性执行。
首次消息前的设置在 root Agent 延迟创建时应用。不支持深度、保留路径数、温度
或逐层 breadth 列表。

`/agents [name]` 和 `/teams [name]` 展示 Registry 解析后的声明及 `local` / `builtin` 来源，查询不落盘。本地定义优先于内置；首次成功编辑才保存覆盖，删除覆盖后恢复内置。内置 Team 详情没有 manifest 路径。`/resources` 及其网关 API 已从命令、帮助和补全中移除。

CLI 空闲时每秒检查一次定时提示词；界面刷新不会重启轮询，上一请求未完成时不会
发送重叠请求。暂停轮询仍保留在途请求已经领取的提示词。
没有到期任务时只读，不创建 `.juice/scheduled_tasks.lock`。关闭 CLI
即停止其轮询，详见 [Cron](../../sdk/src/juice_agents/core/cron/README.zh-CN.md)。

## 代码入口与边界

```text
后端事件 → stdio 通知 → GatewayClient → shared conversation/presenter
                                     → MessageBlock → RowVM → Ink
恢复会话 → describe_agent_sessions 快照 ──────────┘
```

常驻 `stream_step` 与 `ask_request` 通知省略 `params.request_id`。缺失、null 或空 ID
进入常驻处理器；具体 ID 只投递到对应请求，未知具体 ID 丢弃。执行事件和 Ask 使用不同处理器。

消息块、去重状态和渲染行缓存只存在于内存。恢复通过 `describe_agent_sessions` 从后端
`session.json` 和 `inbox.json` 的待处理消息重建，不解析 `log.txt`。CLI 没有独立的展示内容
或事件流持久化目录；`~/.juice/cli_history.jsonl` 仅用于输入回忆，不保存对话展示或工具结果。

| 位置 | 职责 |
| --- | --- |
| `src/entry.tsx`、`src/app.tsx` | 启动、会话状态、界面组装 |
| `src/gateway/client.ts` | stdio 网关通信与请求路由 |
| `src/components/` | 输入框、选择器和任务面板 |
| `src/ink-ext/` | 备用屏和行级滚动；详见[模块 README](src/ink-ext/README.zh-CN.md) |
| `src/lib/` | 命令调度、文本布局和展示模型 |
| `../shared/` | CLI/Web 共用的协议、会话状态和展示逻辑 |

### 开发约定

发起请求结束后仍继续显示自主 round。shared 状态通过 `round_start` / `round_end` 跟踪运行状态，按 `event_id` 去重，并向具体 Runner 与 round 发送取消。切换会话解除后端旧订阅并拒绝其迟到事件。

- 会话消息生命周期复用 `@juice-agents/shared/conversation`；网关协议和 stream/command 展示逻辑复用 shared 包。通过 `presentEvent` 为前台及自主 round 注入同一个 CLI presenter。工具身份使用 Runner、Agent、round 和 `call_id`；名称、索引、步骤号不能用于并发关联。缺少 `call_id` 的工具事件不生成记录，不修改原事件。
- 已提交的 `step_end` 快照按身份更新已有思考/工具记录，补齐遗漏通知；历史复用同一投影，CodeAct 使用保存的 Python wrapper。实时终稿由 `round_end.data.result` 展示。
- 所有 transcript 和输入区域由 Ink 在备用屏中渲染。消息先转换成视觉行，再用 `VirtualScrollList` 截取可见行；总高度由 `computeOverlayBudget` 控制，避免触发 Ink 的清屏路径。不要向 stdout 另写一套消息渲染。
- 选择器、审批和任务面板使用独占输入焦点。面板打开时隐藏输入框；`ask_request` 只通过 `answer_ask` 回复，接受成功后再关闭提问。
- 普通 `Enter` 提交输入，只有 `Alt/Meta+Enter` 换行。运行中的普通消息进入持久化 inbox；已有命令队列或 `/clear` 切换屏障时，后续消息按前端 FIFO 排队。取消会清除当前提问和等待状态，不应阻塞后续消息。
- 共享输入历史使用只追加的 JSON Lines，每条记录保留完整输入；开始浏览时刷新，浏览过程中不改变列表。
- `/mode` 改变当前 Runner 的 Agent 模式，`/permissions` 改变审批策略；`/agent-type` 只修改以后创建的默认 Agent。工作区配置写入 `.juice/config.yaml` 的 `runtime` 节。
- `/clear` 是全局 FIFO 命令，后续提示词必须排在它之后。仅在 `start_session` 成功后清空对话并退出子 Agent 视图；新建失败也可能已经停止原 Runner，必须查询网关校准会话状态。
- 启动恢复、按 ID 恢复与列表选择统一采用返回的 Runner 状态，同时刷新子 Agent 和任务，重置旧面板及未完成的配置切换，并拒绝旧会话加载的迟到 RPC。选择器打开时暂停 FIFO 消费；取消后继续使用当前会话。
- 活动会话以非空 `runner_id` 为准；`started=false` 仍可能是有效的空会话，空 ID 仅表示启动配置预览。
- workspace 在入口统一解析并传递给 Gateway，不通过全局切换进程 cwd 实现。会话和子 Agent 的输入、取消分别使用对应网关请求。
- Cron 轮询回调在渲染间保持稳定；包括空闲/忙碌切换在内，同一时刻最多只有一个检查请求。
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

公开事件使用带类型约束的 `kind + data`，包含 `event_id`、数值 `timestamp`、Runner/Agent 身份及适用的回合/步骤身份。Agent 状态为 `idle/running/released`，回合结果为 `completed/failed/cancelled`。shared reducer 统一维护去重、root/child 对话隔离、工具原位更新与按调用 ID 归并的工作流进度；CLI/Web 共用同一 stream presenter。

新建/恢复遵循 `bindRunner → describe_agent_sessions → applySnapshot → activate_session`；查询可传 `agent_id` 按需读取单个 Agent 历史。取消期间显示“正在停止”，直到真实提交的 `round_end`；取消拒绝、请求失败和断线均不伪造结束。重连后通过 `describe_agent_sessions` 快照校正状态。旧快照格式明确拒绝，原文件保留。

后端 `message_received` 事件使异步完成回执在主 Agent 回复前显示。子 Agent 页面展示
初始任务、用户/Team 消息和历史附件，发送错误也留在当前子页面。快照的 `pending_messages`
补齐 Agent 仍在计算时的待处理消息。调度、投递和消费确认均由后端完成，CLI 只展示事件和快照。
