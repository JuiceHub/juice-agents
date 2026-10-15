> [English](README.md)

# Agent

`core/agent` 定义智能体协议并管理其运行时生命周期。智能体是使用 ReAct 或 CodeAct 策略执行任务的核心推理单元。

## 核心能力

| 功能 | 说明 |
| --- | --- |
| 🧠 **ReAct & CodeAct** | 思考-行动推理（ReAct）或 Python 代码执行（CodeAct） |
| 💾 **会话持久化** | 进程重启后恢复对话和上下文 |
| 🔄 **上下文压缩** | 自动上下文窗口管理，支持增量压缩 |
| 🛠️ **丰富工具集** | 20+ 内置工具：Shell、文件系统、Web、浏览器、图像、MCP、Workflow 等 |
| 📎 **附件注入** | 用户消息、任务通知和异步结果注入 |
| 🎯 **委派协作** | 根智能体可生成和协调辅助智能体 |
| 🔒 **受控执行** | 所有工具调用经过权限和审计边界 |

## 智能体类型

### ReAct Agent

结构化推理和行动：

```xml
<thought>我需要检查当前目录结构</thought>
<actions>[{"name": "list_directory", "args": {"path": "."}}]</actions>
```

**适用场景：** 多步推理、调研、规划、复杂决策。

### CodeAct Agent

执行 Python 代码，包含思考和行动：

```xml
<thought>我需要检查项目结构并读取 README</thought>
<code>
import os
files = os.listdir('.')
print(f"找到 {len(files)} 个文件")

# 通过运行时使用工具
result = tools.read_file('README.md')
print(result)
</code>
```

**适用场景：** 数据处理、快速迭代、计算任务、算法工作流。

## 架构

```text
AgentBinding → AgentRegistry.instantiate() → AgentManager.acquire()
                                              → ManagedAgent
                                                 • 会话恢复
                                                 • 上下文绑定
                                                 • 检查点
                                                 • 取消处理
```

**核心原则：** 智能体是无状态的步骤函数。`AgentManager` 管理其生命周期：

1. **实例化**：从 Registry 获取全新智能体对象
2. **恢复**：从磁盘加载会话快照
3. **绑定**：附加 `RunnerContext` 以进行受控工具执行
4. **执行**：通过 ReAct/CodeAct 循环处理用户请求
5. **检查点**：保存会话状态
6. **释放**：持久化并释放（功能型）或保留（持久型）

## 工具执行流程

智能体不直接拥有工具。所有操作通过 Runner 的权限边界：

```text
Agent.step()
  → RunnerContext.execute_tools(actions)
     → Runner.tool_manager.execute_for_agent()
        → 权限检查
        → 工具绑定
        → 执行
        → 审计日志
        → 返回观测
```

这确保了：
- 统一权限控制
- 取消信号传播
- 所有操作的审计追踪
- 无智能体自有的工作池或执行器

## 内置工具

| 分类 | 工具 |
| --- | --- |
| **文件系统** | `read_file`, `write_file`, `edit_file`, `list_directory`, `search_files` |
| **执行** | `shell`, `python_repl`, `python_script` |
| **Web** | `web_search`, `web_fetch`, `browser_navigate`, `browser_screenshot` |
| **记忆** | `memory_create`, `memory_update`, `memory_search`, `memory_list` |
| **智能体** | `agent_spawn`, `agent_list`, `agent_message` |
| **Workflow** | `workflow_call` |
| **技能** | `skill_view`, `skills_list`, `skill_manage` |
| **输出** | `submit_output`, `ask_user` |
| **任务** | `task_create`, `task_update`, `task_list` |
| **团队** | `team_finish`, `team_members`, `team_broadcast` |

工具根据智能体能力和 Runner 策略动态可用。

## 上下文管理

### 上下文树

智能体上下文以树结构（`ContextTree`）管理，追踪：

```text
Root (TaskStep)
  ├─ ActionStep 1 (思考 + 工具调用 + 观测)
  ├─ ActionStep 2
  │   ├─ 分支 A (探索路径)
  │   └─ 分支 B (备选路径)
  └─ ActionStep 3
```

**核心概念：**

- **ContextNode**：对话中的单个步骤（TaskStep 或 ActionStep）
- **ExplorationPath**：从根到叶的完整轨迹（用于 Best-of-N）
- **AgentStep**：所有步骤类型的抽象基类

### 智能体步骤

**TaskStep** - 用户任务输入：
```python
TaskStep(
    task="分析代码库",
    task_images=[...],        # 可选图像
    attachments=[...]         # 运行时附件
)
```

**ActionStep** - 智能体推理和工具执行：
```python
ActionStep(
    step_num=1,
    thought="我需要先列出文件",
    tool_calls=[{"name": "list_directory", "args": {"path": "."}}],
    observations=["file1.py\nfile2.py\n..."],
    code_action="",           # CodeAct 智能体使用
    reasoning_content="",     # 扩展推理
    round_outcome="continue"  # continue|submitted|failed；只有 submit_output 结束本轮，空 actions 继续下一步
)
```

**SummaryStep** - 压缩历史：
```python
SummaryStep(
    content="[前 10 步的压缩摘要...]"
)
```

### 会话持久化

会话在重启后完整恢复：

```python
# 首次运行
agent = manager.acquire("root", binding, workspace)
result = agent.step(user_input)
manager.checkpoint(agent)  # 保存上下文树到磁盘

# 重启后（甚至不同进程）
agent = manager.acquire("root", binding, workspace)
# 上下文树自动恢复：
#   - 完整对话历史
#   - 工具执行结果
#   - 压缩部分（如有）
#   - 节点结构和元数据
result = agent.step(continuation_input)
```

**检查点包含：**
- 包含所有节点的 `ContextTree`
- 系统提示词（静态 + 动态）
- 探索路径和选中路径
- 压缩状态
- 智能体特定配置

**存储位置：** `.juice/runners/<runner_id>/agents/<agent_id>/session.json`

### 消息序列化

步骤转换为模型消息：

```python
# TaskStep → user 消息
{"role": "user", "content": [{"type": "text", "text": "<task>...</task>"}]}

# ActionStep → assistant + user 反馈
{"role": "assistant", "content": [{"type": "text", "text": "<thought>...</thought>..."}]}
{"role": "user", "content": [{"type": "text", "text": "<observations>...</observations>"}]}
```

树追踪当前活动路径并将其转换为发送给模型的消息序列。

## 会话持久化

会话在进程重启后完整恢复：

```python
# 首次运行
agent = manager.acquire("root", binding, workspace)
result = agent.step(user_input)
manager.checkpoint(agent)  # 保存到磁盘

# 重启后（甚至不同进程）
agent = manager.acquire("root", binding, workspace)
# 会话自动恢复
result = agent.step(continuation_input)
```

**持久化内容：**
- 完整的 ContextTree（所有节点和路径）
- 系统提示词（静态 + 动态）
- 压缩状态
- 智能体配置

**存储位置：** `.juice/runners/<runner_id>/agents/<agent_id>/session.json`

**工作原理：**
1. `manager.checkpoint()` 原子写入会话
2. `acquire()` 时，manager 从磁盘加载快照
3. 从 Registry 创建全新的 Agent 实例
4. 将会话恢复到 Agent
5. Agent 从中断处继续

## 上下文压缩

接近 token 限制时，Juice 自动压缩历史，同时保留关键信息。

### 压缩策略

**CompactCompressStrategy** - 激进压缩：
```python
# 将旧步骤压缩为单个 SummaryStep
# 保留：当前任务、最近步骤、未完成操作
# 结果：最大 token 节省
```

**RemainCompressStrategy** - 保留最近上下文：
```python
# 保持最近 N 步完整
# 仅压缩更早的历史
# 结果：更好的上下文质量，较少节省
```

**NoCompressStrategy** - 禁用：
```python
# 无压缩（达到限制时失败）
```

### 增量压缩

压缩是增量的 - 仅处理自上次压缩以来的新内容：

```text
压缩前：
  TaskStep → ActionStep1 → ... → ActionStep10

首次压缩后（压缩步骤 1-7）：
  TaskStep → SummaryStep(1-7) → ActionStep8 → ActionStep9 → ActionStep10

更多步骤 + 第二次压缩后：
  TaskStep → SummaryStep(1-7) → SummaryStep(8-12) → ActionStep13 → ActionStep14
```

**永不压缩：**
- 当前 TaskStep
- 上次压缩后的步骤
- 活动观测
- 未完成的工具调用

**压缩触发：**
- 接近上下文窗口限制（可配置阈值）
- 通过压缩 API 手动触发

**存储：** 压缩状态被检查点，因此恢复的会话不会重新压缩。

## 附件

运行时附件向智能体会话注入信息：

```python
# 用户消息
attachment = UserMessageAttachment(content="额外上下文")
agent.attach(attachment)

# 任务通知
attachment = TaskNotificationAttachment(
    task_id="task_123",
    status="completed",
    result={"output": "..."}
)
agent.attach(attachment)
```

附件在智能体的下一步中作为系统消息出现。

## 模块结构

| 文件 | 用途 |
| --- | --- |
| `agents.py` | `MultiStepAgent`、`ReActAgent`、`CodeActAgent` 协议 |
| `manager.py` | `AgentManager` - 生命周期、快照、检查点 |
| `attachments.py` | 运行时附件协议 |
| `tools/` | 工具声明、运行时绑定和适配器 |
| `context/` | 上下文树、压缩、序列化 |
| `prompts/` | 系统提示词和提示段 |

## 开发约定

- **全新实例**：`AgentManager.acquire()` 始终从 Registry 实例化全新智能体，然后恢复会话。活动智能体永不原地重建。
- **无工具所有权**：智能体仅通过 `RunnerContext` 执行工具。不拥有 `ToolManager`、线程池或执行器实例。
- **两种生命周期**：`persistent` 智能体保留其实例；`functional` 智能体在每次请求后检查点并释放。
- **原子持久化**：检查点是原子写入。所有日志包含 Runner/Agent ID。
- **计划模式**：规划是根智能体的能力，不是特殊的智能体类型。没有保留的 `plan` 智能体声明。

在 `tests/core/agent/` 添加生命周期测试，在 `tests/core/managers/` 添加工具路由测试。
