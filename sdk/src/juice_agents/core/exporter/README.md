# Trajectory Exporter

把 juice-agents 持久化的运行轨迹导出为可直接用于 post-training 的数据集。

## 核心能力

- **OpenAI Chat Completions 格式**：JSONL，每行一条样本，user / assistant 严格交替，适用于所有支持 chat 模板的模型
- **三种导出粒度**：单个 runner、多个 runner、整个 workspace
- **可控的内容保留**：通过 `ExportConfig` 控制保留哪些会话、保留哪些内容
- **流式写入**：大数据集不会撑爆内存

## 数据来源

读取 workspace 下 `.juice/runners/<runner_id>/agents/<agent_id>/session.json`。快照由
`AgentManager` 持久化，导出器通过 `JsonAgentSnapshotStore` 与 sessions 反序列化层读取，
不重复实现磁盘格式解析。

## 输出格式

每行一个 JSON 对象：

```json
{
  "messages": [
    {"role": "system",    "content": "<system prompt>"},
    {"role": "user",      "content": "<task>用户任务</task>"},
    {"role": "assistant", "content": "<thought>...</thought>\n<actions>[...]</actions>"},
    {"role": "user",      "content": "<observations>\n<result_of_action_0>...</result_of_action_0>\n</observations>"}
  ],
  "metadata": {
    "runner_id": "11a94b3a",
    "agent_id": "root",
    "agent_name": "general",
    "agent_role": "root",
    "mode_id": "agent",
    "is_root": true,
    "created_at": 1718436000.0
  }
}
```

会话 → 消息映射：

| 来源 | 消息 |
| --- | --- |
| `AgentSession.system_prompt` | `{"role": "system"}` |
| `TaskStep` | `{"role": "user", "content": "<task>...</task>"}` |
| `ActionStep.model_output` | `{"role": "assistant"}` |
| `ActionStep.observations` | `{"role": "user", "content": "<observations>...</observations>"}` |
| `SummaryStep` | `{"role": "user"}` |

转换后会自动合并相邻同 role 消息，保证 user/assistant 严格交替（post-training 框架硬性要求）。

## 快速开始

```python
from juice_agents.core.exporter import TrajectoryExporter

exporter = TrajectoryExporter(base_dir=".")

# 1) 导出单个 runner
exporter.export_runner("11a94b3a", "out/r1.jsonl")

# 2) 批量导出
exporter.export_multiple_runners(["r1", "r2"], "out/batch.jsonl")

# 3) 导出整个 workspace
result = exporter.export_workspace("out/all.jsonl")
print(result.num_samples, result.num_messages)
```

## 配置项

`ExportConfig`（或等价的 dict）控制三类决策：

```python
from juice_agents.core.exporter import ExportConfig, TrajectoryExporter

config = ExportConfig(
    # —— 会话级过滤 ——
    success_only=True,        # 只保留 terminal 且无 error 的会话
    min_action_steps=2,       # 至少有 2 个 ActionStep
    max_action_steps=20,      # 最多 20 个 ActionStep

    # —— 内容级开关 ——
    include_system_prompt=True,
    include_observations=True,
    include_reasoning=False,  # 是否前置 <think> 块
    include_summary_steps=True,

    # —— 清理选项 ——
    truncate_observations=8000,  # 单条观测最大字符数
    drop_error_steps=False,      # 丢弃带 error 的 ActionStep
)

TrajectoryExporter(".").export_workspace("out.jsonl", config=config)

# 也支持 dict（未知键会被忽略）
TrajectoryExporter(".").export_workspace(
    "out.jsonl",
    config={"success_only": True, "min_action_steps": 2},
)
```

默认是「完整保留」：所有会话 + 所有内容都导出，调用方按需收紧。

## 返回值

```python
result = exporter.export_workspace("out.jsonl", config={"success_only": True})

result.output_path           # 实际写出的文件路径
result.num_samples           # 写出的样本数
result.num_messages          # 所有样本的消息总数
result.num_sessions_scanned  # 扫描过的会话数（含被过滤的）
result.num_sessions_skipped  # 被会话级过滤丢弃的会话数
result.runner_ids            # 实际贡献样本的 runner id 列表
result.as_dict()             # 转 dict 便于打印 / 序列化
```

## 注意事项

- 图片观测无法进入纯文本序列，降级为 `[image: <description>]` 占位
- 没有 `ActionStep` 的会话或合并后无对话内容（只有 system）的样本会被自动跳过
- 输出文件父目录会自动创建；空导出也会生成空文件，便于流水线统一处理
- **隐私提醒**：导出数据不会自动脱敏。导出后请人工审视 `system_prompt` / `observations` 是否包含敏感信息（凭据、绝对路径、用户数据）再用于训练

## 开发约束

| 文件 | 职责 |
| --- | --- |
| `filters.py` | 不可变 `ExportConfig` 与无副作用过滤判断 |
| `formats.py` | `AgentSession` 到对话样本的纯转换 |
| `exporters.py` | 快照读取、过滤和流式 JSONL 写出 |

- 读取 Runner manifest 与 `JsonAgentSnapshotStore` 快照，再由 session 反序列化层恢复数据；不重新解析或复制旧磁盘格式。损坏的 manifest/快照记录 warning 并跳过。
- 保持消息转换后 user/assistant 交替。新 step 类型必须覆盖消息进出；默认过滤配置保持完整保留语义。图片只输出文本占位，`tool_calls` 默认保留在协议文本里。
- 顺序写入每条样本，不缓存整个数据集。新增格式时在 `formats.py` 平行实现并由 `exporters.py` 分发；测试放 `tests/core/exporter/`，fixture 使用生产持久化 API。
