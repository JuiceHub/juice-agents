> [English](README.md)

# Agent 运行时管理器

`AgentManager` 统一拥有活跃实例、串行 round 调度、快照和持久 inbox。
通过 `juice_agents.core.managers` 导入；Agent 协议与上下文树保留在 `core/agent/`。

```python
from juice_agents import Runner
from juice_agents.core.managers import AgentManager

async def answer(workspace):
    async with await Runner.acreate(base_dir=workspace) as runner:
        manager: AgentManager = runner.agent_manager
        result = await runner.arun_round("说明这个项目")
        return manager.round_result(runner.root_agent_id, result.round_id)
```

| 文件 | 职责 |
| --- | --- |
| `manager.py` | 获取/释放实例、快照、候选检查点、终态提交 |
| `inbox.py` | 输入落盘、消费确认、协程调度和取消 |

异步执行接口为 `aacquire/arun/astream/apersist/arelease/aclose`，`apost` 接受
持久输入。persistent round 统一进入 inbox 调度，functional 调用在返回前释放实例。
一个会话由单个运行中的 Runner 持有；`round_result(agent_id, round_id)` 可读取后续
回合开始前已提交的历史结果。

上下文、已消费消息 ID 和 round 检查点同次保存到 `session.json`。尚未开始的输入
被取消后，直接提交取消结果，不发模型请求；关闭调度等待已启动工作收尾。
模型调用、用量及连接归 ModelManager，AgentManager 通过显式回调绑定身份；
团队分派和结果对账归 TeamManager。

功能测试位于 `tests/core/managers/agents/`，Agent 协议和上下文测试保留在
`tests/core/agent/`。同步/异步入口复用执行链，生命周期日志包含 Runner、Agent
和 round 标识。
