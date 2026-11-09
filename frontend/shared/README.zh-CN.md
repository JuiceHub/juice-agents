> [English](README.md)

# Frontend shared

`frontend/shared` 是 CLI 与 Web 共用的 TypeScript 包，提供网关协议类型、会话消息状态和展示模型。
安装步骤见[项目 README](../../README.zh-CN.md)。

| 导出 | 用途 |
| --- | --- |
| `@juice-agents/shared/gateway/types` | 网关请求、响应和事件类型 |
| `@juice-agents/shared/conversation` | 流式消息与会话状态 |
| `@juice-agents/shared/presenter/stream` | 将流事件转换成消息块 |
| `@juice-agents/shared/presenter/command` | 将命令结果转换成展示内容 |


公开事件使用带类型约束的 `kind + data`，包含 `event_id`、数值 `timestamp`、Runner/Agent 身份及适用的回合/步骤身份。Agent 状态为 `idle/running/released`，回合结果为 `completed/failed/cancelled`。shared reducer 统一维护去重、root/child 对话隔离、工具原位更新与按调用 ID 归并的工作流进度；CLI/Web 共用同一 stream presenter。

新建/恢复遵循 `bindRunner → describe_agent_sessions → applySnapshot → activate_session`；查询可传 `agent_id` 按需读取单个 Agent 历史。取消期间显示“正在停止”，直到真实提交的 `round_end`；取消拒绝、请求失败和断线均不伪造结束。重连后通过 `describe_agent_sessions` 快照校正状态。旧快照格式明确拒绝，原文件保留。

`message_received.data.attachments` 展示后端已接受的消息，异步完成回执无需等主 Agent
回复即可显示；`round_start.data.input` 展示已保存的初始任务，也覆盖 functional 子调用。
恢复时读取 TaskStep/ActionStep 的已保存附件和快照的 `pending_messages`，按稳定消息 ID
归并实时、待处理和历史记录。
inbox、调度、确认消费及任务结果均属于后端；presenter 只格式化后端提供的正文和状态。

stdio 常驻订阅省略 `request_id`，协议类型将其标记为可选。缺失、null 或空 ID 进入常驻
事件/Ask 处理器；具体 ID 只投递到对应请求。会话展示状态和渲染缓存只在内存保存，
恢复读取后端快照，不依赖前端事件流持久化。

## 开发约定

- Agent 与 Team 展示 Registry 的有效来源（`local` / `builtin`）。`TeamInfo` 和 `TeamManifest` 包含 `source` 与可空的 `manifest_path`，内置 Team 不展示不存在的文件。首次成功编辑保存本地覆盖，删除覆盖后恢复内置。资源查询使用 `/agents` 和 `/teams`；`/resources`、专用 presenter、协议类型与 `list_mode_resources` 请求已移除。
- `WorkspaceRuntimeConfig` 同时描述有效配置与稀疏项目补丁。CLI 的 `load_effective_config` 和 Web 的 `effective_config` 由后端统一合并；`load_workspace_config` 仍只读项目覆盖。保存时只传用户修改的字段，不将继承默认值写回项目。
- 网关类型与 Python 网关序列化结果保持一致；CLI 和 Web 不分别维护协议副本。
- `StrategyConfiguration` 仅含正整数 `breadth` 或 `reset`；`StrategySwitchResult` 区分 pending/applied 配置状态与 linear/candidate_selection 执行方式，并说明是否配置评分器。
- `RewardModelConfiguration` 对齐 `configure_reward_model` 返回值：目录模型名 `model_name`（或 `null`）、评分器状态、保持不变的 `breadth` 和实际执行方式。`/rm` 打开复用 `/models` 目录的选择页，标记当前奖励模型并提供清除选项；保留 `/rm <model_name>` 和 `/rm reset`。选择或清除均保留候选数和生成模型；网关负责构建评分器和解析密钥。
- `ImageModelsResponse` 对齐只读 `list_image_models`：独立生图目录、当前生图模型和开关；`RuntimeImageModelInfo` 仅包含公开 Provider 元数据。`set_image_config` 独立接受可选的 `model_name` 与 `enabled`，返回 `ImageConfigResponse`（`enabled`、`runner_id`、`image_model_name`）；选模型不能自动开启生图工具或改变聊天/奖励模型配置。
- presenter 只根据输入生成展示模型，不修改 Runner 或任务状态。Team 更新从 `event.data.snapshot` 读取任务、成员和完成进度；`eligible_members` 表示可执行范围，`claimed_by` 表示实际领取人。
- 恢复历史使用已提交的 `round_result` 展示最终回答和失败；仅当该回合没有结果记录时，才使用 submitted action 的输出。实时流通过 `round_end` 展示最终回答，并复用相同回合身份。
- 切换会话后，用返回的 ID 调用 `bindRunner(runnerId)`，在 React 提交状态前重置回合跟踪并拒绝旧会话事件；调用方同时替换该会话的历史和展示状态。
- 会话状态和取消行为由 `conversation` 统一处理；界面组件只负责显示和交互。恢复快照重建活动回合，并校正主对话、子 Agent 历史、工具状态和进度；界面不得再次追加主对话历史。
- 带身份的工具块按 Runner、Agent、round、`call_id` 原位更新；不可变替换刷新终端行缓存，并保留并发调用的开始顺序。`ToolCallBlock.observation` 保存返回文本，与 `error` 分离。状态仅使用已有执行字段及别名，不分析输出文本；缺少状态则保持 unknown。已提交的 `step_end` 快照更新同一记录并补齐遗漏通知，历史复用同一生命周期投影。
- `conversation` 按 Runner 与 round ID 跟踪 `round_start` / `round_end`，支持无请求 ID 的自主事件；按 `event_id` 去重订阅与请求流，前台请求结束不清除其它运行中 round，并拒绝旧 Runner 事件。hook 传入当前 `runnerId`，取消时发送具体 Runner 与 round 身份。
- Ask 请求归属具体 Runner 和回合，只有该回合或同步调用方的取消回合结束时才清除交互；root 回合结束不会清除独立子任务的 Ask。
- 无活动会话时传 `runnerId: ""`，拒绝已解绑会话的事件；创建后立即发送时使用 `sendMessage(text, modeOverride, runnerId)` 传入返回的 ID，在 React 提交状态前绑定首条请求。切换作用域保留可见记录，仅在创建成功后清空。会话产物路径为 `.juice/sessions/<runner_id>/`。
- 新增跨界面逻辑时先判断能否放入此包，并为纯逻辑添加有意义的回归测试。

在仓库根目录验证：

```bash
conda activate juice-agents
cd frontend
npm test --workspace @juice-agents/shared
npm run typecheck --workspace @juice-agents/shared
npm run build --workspace @juice-agents/shared
```
