# 业务示例（examples）

四个示例使用真实模型演示完整业务流程。每个示例文件夹既是独立入口，也是固定工作区；配置、Runner 状态和模型产物保存在该文件夹的 `.juice/` 中。

| 场景 | 命令 | 结果 |
| --- | --- | --- |
| 可追溯研究 | `python -m examples.agent_research` | 报告、sources、claims 和 Graph trace |
| 能力演化 | `python -m examples.agent_evolution` | 创建并调用可复用 Tool 或 specialist |
| 反馈运营 | `python -m examples.group_operations` | 反馈分类、优先级和行动建议 |
| 团队交付 | `python -m examples.team_delivery` | Team task/inbox、代码产物和验证结果 |

## 运行

先在仓库根目录进入环境，并为**要运行的示例**配置模型和 provider 密钥：

```bash
conda activate juice-agents
python -m pip install -e ./sdk
mkdir -p examples/agent_research/.juice
cp sdk/src/juice_agents/_assets/config.example.yaml examples/agent_research/.juice/config.yaml
# 编辑 config.yaml，填写模型配置；按所用 provider 设置对应环境变量
python -m examples.agent_research "比较客户门户的托管与自管部署方案"
```

其他示例的 `.juice/config.yaml` 要分别配置。示例不会清理或覆盖已有 Runner 状态。常用命令：

```bash
python -m examples.agent_evolution "为支持升级创建 release note 检查能力"
python -m examples.group_operations "审阅反馈并给出本周运营计划"
python -m examples.team_delivery "实现 TASK.md 中的需求并完成验证"
python -m examples.team_delivery --resume <runner_id> "继续未完成任务并报告验证结果"
```

四个入口均支持 `--model`、`--model-effort`、`--agent-type react|codeact`、`--permission-mode default|accept` 和 `--resume RUNNER_ID`。不传 `--resume` 时新建 Runner；运行时输出 Runner ID 和事件进度，结束时释放 Runner。需要恢复时使用输出的 ID。示例只使用真实模型，没有离线模拟分支。

## 开发约定

```text
动态业务输入 ──> 场景系统指令 ──> Juice(workspace=示例目录) ──> Runner stream
                                                           └──> .juice/ 状态与产物
```

- 用户提示词只放动态业务信息；流程、边界和验收标准写入场景系统指令。
- 每个场景从 `__main__.py` 进入，使用公开的 `Juice(...).runners.create/resume`，并以自身目录作为 workspace；不增加 `--workspace` 选项。通用参数、事件渲染和 Runner 生命周期复用 `examples/_runtime.py`。
- 场景使用专属工作区配置的真实模型；不加入 mock、假产物或静默降级。共享运行时在成功和异常路径都调用 `runner.stop()`。
- 新增或修改场景时，在 `tests/examples/` 加功能测试，并更新本 README 与[项目 README](../README.md)。
