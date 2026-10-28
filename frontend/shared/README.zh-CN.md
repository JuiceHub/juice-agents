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

## 开发约定

- Agent 与 Team 展示 Registry 的有效来源（`local` / `builtin`）。`TeamInfo` 和 `TeamManifest` 包含 `source` 与可空的 `manifest_path`，内置 Team 不展示不存在的文件。首次成功编辑保存本地覆盖，删除覆盖后恢复内置。资源查询使用 `/agents` 和 `/teams`；`/resources`、专用 presenter、协议类型与 `list_mode_resources` 请求已移除。
- `WorkspaceRuntimeConfig` 同时描述有效配置与稀疏项目补丁。CLI 的 `load_effective_config` 和 Web 的 `effective_config` 由后端统一合并；`load_workspace_config` 仍只读项目覆盖。保存时只传用户修改的字段，不将继承默认值写回项目。
- 网关类型与 Python 网关序列化结果保持一致；CLI 和 Web 不分别维护协议副本。
- `StrategyConfiguration` 仅含正整数 `breadth` 或 `reset`；`StrategySwitchResult` 区分 pending/applied 配置状态与 linear/candidate_selection 执行方式，并说明是否配置评分器。
- `RewardModelConfiguration` 对齐 `configure_reward_model` 返回值：目录模型名 `model_name`（或 `null`）、评分器状态、保持不变的 `breadth` 和实际执行方式。`/rm` 打开复用 `/models` 目录的选择页，标记当前奖励模型并提供清除选项；保留 `/rm <model_name>` 和 `/rm reset`。选择或清除均保留候选数和生成模型；网关负责构建评分器和解析密钥。
- presenter 只根据输入生成展示模型，不修改 Runner 或任务状态。Team 更新从 `team_event.snapshot` 读取任务、成员和完成进度；`eligible_members` 表示可执行范围，`claimed_by` 表示实际领取人。
- 会话状态和取消行为由 `conversation` 统一处理；界面组件只负责显示和交互。
- 新增跨界面逻辑时先判断能否放入此包，并为纯逻辑添加有意义的回归测试。

在仓库根目录验证：

```bash
conda activate juice-agents
cd frontend
npm test --workspace @juice-agents/shared
npm run typecheck --workspace @juice-agents/shared
npm run build --workspace @juice-agents/shared
```
