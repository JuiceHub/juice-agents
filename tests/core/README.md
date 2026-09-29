# Core 测试

`tests/core/` 按 SDK 的功能域组织回归测试；端到端适配层、示例和包构建测试分别位于
`tests/adapters/`、`tests/examples/` 和 `tests/sdk/`。

| 目录 | 主要验证内容 |
| --- | --- |
| `agent/` | Agent 会话、模型协议与内置工具 |
| `registry/` | 声明解析、校验与运行时边界 |
| `runner/` | 请求、恢复、后台任务与多 Agent 调度 |
| `graph/` | StateGraph 流程和 checkpoint |
| `team/` | Team 任务与成员协作 |

从仓库根目录运行：

```bash
conda activate juice-agents
python -m pytest tests/core -q
```

测试应调用被测行为，使用临时工作区并在结束时清理文件。只有目录结构、依赖方向
等无法通过运行时行为验证的约束才使用源码或目录检查；这类检查必须先断言确实扫到
目标文件。不要用 `skip` 掩盖回归，也不要保留一次性迁移测试。新增 `test_*.py`
的目录需含 `__init__.py`，避免 `unittest discover` 漏收。
