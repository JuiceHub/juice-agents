> [English](README.md)

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

Web 服务默认监听 `127.0.0.1:8003`。它提供会话和模型状态、工作区内的只读文件预览、Runner 事件流与审批交互，以及当前 Runner 的浏览器预览和控制。stdio 服务提供相应的 Runner 会话、模型、配置、Skill/Plugin/Team 与流式消息 RPC；CLI 通常负责启动它。

会话、浏览器状态、截图和观察图片统一保存在 `.juice/sessions/<runner_id>/`；
会话发现、恢复和 Web 归档/还原均使用此路径。旧 `.juice/runners/` 数据不读取、不迁移。
CLI 与 Web 的 `/clear` 在当前回合及更早排队的输入完成后创建空白会话，旧会话仍可通过 `/resume` 恢复。
同工作区新建时，未显式传入的模型、effort、模式、Agent 类型、权限、模型目录来源、策略和奖励模型沿用当前选择；
显式参数优先，跨工作区不继承。替换配置先校验再关闭旧会话；关闭后若创建失败，
`describe_session` 返回空 `runner_id`，前端据此清除失效目标并保留可见历史。

stdio 的 `switch_strategy` RPC 接受 `{"breadth": 3}` 或 `{"reset": true}`。
独立的 `configure_reward_model` RPC 接受 `{}` 查询、`{"model_name": "<目录名>"}`
设置或 `{"reset": true}` 清除。`GenerativeRewardModel.from_config(name, context)`
直接调用 provider SDK 生成 JSON 评分并解析为数值，复用 `list_models` 的目录和配置上下文，
构造及空批次均不解析 provider。SDK 调用方也可将
`GenerativeRewardModel(model_name="your-model", client=sdk_client)` 注入策略，无需配置文件；
`sdk_client` 是与 backend 对应的原生 SDK client。
配置了评分器且 `breadth>1` 时，每步生成候选、一次请求批量
评分有效候选，并只执行选中项；其他情况保持线性执行。清除奖励模型保留候选数，
重置策略恢复单候选并保留目录奖励模型设置。响应报告实际执行状态；同一工作区
重新启动/恢复会话时转交逻辑配置，设置仅在当前进程内有效，不序列化到会话。

Agent 和 Team 查询统一通过 SDK Registry 按“本地声明优先、只读内置兜底”解析。冷查询不创建 `.juice`；新建 Runner 只保存运行状态，不保存预置 Agent YAML 或 Team manifest。首次成功编辑才保存完整本地覆盖，删除覆盖后恢复内置。响应的 `source` 为 `local` 或 `builtin`，内置 Team 的 `manifest_path` 为 `null`。资源查询使用 `list_available_agents`、`list_team_configs`、`get_team_config`；专用 `list_mode_resources` RPC 已移除，旧请求按未知接口处理。

## 配置与工作区契约

CLI 安装器绑定仓库/Python/Node，Gateway 子进程的 cwd 为目标 workspace，并显式添加仓库和
`sdk/src` 模块路径；不覆盖用户 `PATH`。SDK 不依赖 `os.chdir()`。

| 接口 | 行为 |
| --- | --- |
| `load_effective_config` / `GET /api/effective-config` | 只读合并基础配置、`~/.juice/config.yaml`、项目覆盖和当前启动选择 |
| `load_workspace_config` | 只读项目 `.juice/config.yaml`，保留原有语义 |
| `save_workspace_config` / `PUT /api/workspace-config` | 合并用户实际修改的字段到项目配置 |
| `list_image_models` | 只读返回 `image_models`、`current_image_model_name`、`image_enabled`，无需 Runner 或密钥，不写文件 |
| `set_image_config` | 可选 `{model_name, enabled}` 生图配置补丁；选模型不改变开关 |
| `switch_agent_mode` | 省略 `agent_type` 时保留当前类型；显式传 `react` 或 `codeact` 才切换类型 |
| Web bootstrap | 同时返回 `workspace_config` 与 `effective_config`，界面使用后者初始化 |

启动选择通过 Runner 的 `runtime_overrides` 传入，不将继承默认值整份保存到项目。
会话启动前后切换模式均保留继承或显式选择的 Agent 类型，不创建额外的项目配置覆盖。

聊天与 GRM 共用 `models`，图片使用 `image_models`，均引用共享 `providers`。
生图 RPC 的 `model_name` 不会污染聊天模型。`set_image_config` 返回
`{enabled, image_model_name, runner_id}`；写入前检查目录、启用所需凭据及繁忙状态，
在 Runner 配置锁内保存并调用 `reconfigure(reload_config=True)`，保留会话、历史、GRM 和候选数。
stdio 和 WebSocket 同步支持；CLI 提供 `/image_model`，Web 首版保留开关，不新增图片模型面板。
已有 Git worktree 可直接作为 workspace；内置 worktree 管理返回明确的未实现提示。

## Round 消息投递

```text
start/resume → 绑定 Ask 与 subscribe_events → describe_agent_sessions
             → activate_session(runner_id) → round_start → … → round_end
```

创建或恢复时先绑定常驻订阅，客户端载入初始快照后再调用
`activate_session(runner_id)` 启动恢复调度。事件仅由常驻订阅发送一次，不附带
前台请求 ID 或会话正文副本。`stream_message` 等待并返回
`{done: true, result: {round_id, status, output, reason, error}}`；界面只依据
Manager 已提交的 `round_end.data.result` 结束回合。

公开事件采用 `kind + data`，保留 `event_id`、`timestamp`、`runner_id`、Agent
身份及适用的回合、步骤标识。`describe_agent_sessions` 支持可选 `agent_id`，
用于恢复初始历史或运行中按需查看某个 Agent。`cancel_stream({runner_id, round_id})`
仅请求取消对应回合；接受后唤醒该调用树中的同步审批，保留独立后台审批，
拒绝时保留等待。`round_start` 前可用已排队的 `{request_id}` 设置取消信号，
仍由 SDK 提交和发布真实取消结果。`stop_session` 等待已开始的工作收尾后解除订阅；
切换会话解除旧 Runner 订阅。
stdio 将 `answer_ask` 按 Ask 身份送入独立队列。恢复旧版本快照时明确报错，
保留原文件且不自动迁移。

## 开发约定

SDK 模型提供原生异步生成和评分。网关命令协议保持现有约定，
Web 直接 await `Runner.arun_round()`，stdio 使用同一协程执行链的同步入口；
评分器的异步配置重载与清理在模型所属循环执行。

```text
CLI / Web ── JSON-RPC / HTTP / WebSocket ──> adapters ──> juice_agents Runner
```

- 依赖只能从 `adapters` 指向 `juice_agents`。新增协议入口放在 `adapters/` 的独立子目录；不要在接入层复制 Runner、Agent、Workflow 或配置存储逻辑。
- 从 `juice_agents` 导入 `Runner`，直接调用 `Runner.create/resume(base_dir=workspace, ...)`；创建和恢复后均通过 `runner.set_ask_handler(...)` 绑定当前界面的交互回调，回调不持久化。
- stdio 的 stdout 只承载 JSON-RPC；日志写 stderr。stdio 和 WebSocket 共用事件序列化，原样透传 Runner 的 Team 任务状态、session 标题预览及模型能力信息。
- 有效配置复用 SDK 分层读取；只通过 SDK 的配置接口持久化项目覆盖。冷查询 Plugin、Skill 和 Team 配置时不创建 Runner；仅在已有 Runner 上重装配变更。
- 策略和奖励模型配置仅保留在接入层内存中；通过 Manager 的运行时绑定钩子应用到惰性或重新取得的 root。评分从 `juice_agents.core.models.reward_models` 导入 `GenerativeRewardModel`，协议 handler 不直接调用 provider。
- Web 服务没有身份认证，仅允许监听 loopback 地址；HTTP/WebSocket 的 Host 与浏览器 WebSocket 的 Origin 均须限制为本机。文件预览只能读取工作区内路径，符号链接目录不能展开到工作区外。
- 浏览器控制复用当前 Runner 的 Playwright session。Playwright 对象只能由所属线程操作；`/ws/browser/live` 负责导航和输入，`/api/browser/live/new-tab` 创建真实 tab。
- 流式传输期间仍须接收 `answer_ask` 和 `cancel_stream`。取消审批等待时要返回取消结果，错误的 `request_id` 要报告错误并继续等待正确答复。

框架安装和整体使用方式见[项目 README](../README.zh-CN.md)。

Web 流直接 await `Runtime.astream_message()` 和 `Runner.arun_round()`，不再按回合占用等待线程；stdio 使用同一执行链的同步入口。
