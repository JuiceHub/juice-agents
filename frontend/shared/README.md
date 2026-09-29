# Frontend shared

`frontend/shared` 是 CLI 与 Web 共用的 TypeScript 包，提供网关协议类型、会话消息状态和展示模型。
安装步骤见[项目 README](../../README.md)。

| 导出 | 用途 |
| --- | --- |
| `@juice-agents/shared/gateway/types` | 网关请求、响应和事件类型 |
| `@juice-agents/shared/conversation` | 流式消息与会话状态 |
| `@juice-agents/shared/presenter/stream` | 将流事件转换成消息块 |
| `@juice-agents/shared/presenter/command` | 将命令结果转换成展示内容 |

## 开发约定

- 网关类型与 Python 网关序列化结果保持一致；CLI 和 Web 不分别维护协议副本。
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
