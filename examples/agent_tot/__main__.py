"""每步生成多个候选、评分择一执行的探索示例。

直接运行本文件即可演示 ToT；默认使用生成模型批量评分，也可通过
--reward-model 选择目录中的独立模型，或 main(..., reward_model=scorer) 注入。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

# Direct-file execution starts sys.path in this example's folder, not the repo.
# Use one canonical package import for both IDE Run and `python -m` entry points.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples._runtime import add_runtime_arguments, run_showcase, showcase_workspace
from juice_agents.core.agent.context.steps import ActionStep
from juice_agents.core.agent.context.strategies import (
    GreedyStrategy,
    TreeOfThoughtsStrategy,
)
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.config.model_catalog import DEFAULT_RUNTIME_MODEL_NAME, list_runtime_models
from juice_agents.core.models.reward_models import BaseRewardModel, GenerativeRewardModel
from juice_agents.core.registry.agents.types import DEFAULT_MODEL_CONFIG_NAME


DEFAULT_TASK = (
    "实现一个容量可配置的 Python LRU 缓存，get/put 均为 O(1)。"
    "交付 lru_cache.py 和 tests/test_lru_cache.py，验证更新键、超容量淘汰和未命中，"
    "并报告实际测试结果。"
)


class DemonstrationStrategy(TreeOfThoughtsStrategy):
    """Show the real scoring decision before the winner executes its actions."""

    def select(
        self, *, context: list[dict[str, Any]], candidates: list[ActionStep],
        on_model_usage: Callable[..., None] | None = None,
    ) -> tuple[int, list[float | None]]:
        selected, scores = super().select(
            context=context, candidates=candidates, on_model_usage=on_model_usage,
        )
        print(f"[tot] candidates={len(candidates)} scores={scores} selected={selected + 1}", flush=True)
        for index, candidate in enumerate(candidates):
            # Show proposed actions, not the potentially lengthy reasoning. The
            # full candidates and scores remain available in persisted context.
            proposal = (
                json.dumps(candidate.tool_calls, ensure_ascii=False)
                if candidate.tool_calls else candidate.code_action
            )
            if not proposal:
                proposal = str(candidate.error or "No tool actions")
            print(f"  candidate {index + 1}: {proposal[:300]}", flush=True)
        return selected, scores


def _positive_int(value: str) -> int:
    """Reject invalid breadth before creating workspace state or resolving models."""

    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("breadth 必须为正整数")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m examples.agent_tot",
        description="每步候选选择：生成 breadth 个候选后评分，只执行选中的输出。",
    )
    parser.add_argument("task", nargs="?", default=DEFAULT_TASK, help="要执行的任务；默认演示 LRU 缓存实现和测试")
    parser.add_argument(
        "--strategy", choices=("greedy", "tot"), default="tot",
        help="tot（默认）每步评分择优；greedy 用于对照线性执行。",
    )
    parser.add_argument("--breadth", type=_positive_int, default=3, help="每步候选数量（默认 3）")
    parser.add_argument("--reward-model", metavar="MODEL_NAME", help="目录中的奖励模型名；默认复用生成模型")
    add_runtime_arguments(parser)
    return parser


def main(argv: list[str] | None = None, *, reward_model: BaseRewardModel | None = None) -> Any:
    """Run a real, scored demonstration using the example's model catalog."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.reward_model and reward_model is not None:
        parser.error("--reward-model 不能与 Python 注入的 reward_model 同时使用")
    if args.strategy == "greedy" and (args.reward_model or reward_model is not None):
        parser.error("greedy 不使用评分器；请移除奖励模型设置或选择 tot")
    workspace = showcase_workspace(__file__)

    def configure_runner(runner: Any) -> None:
        scorer = reward_model
        if args.strategy == "tot" and scorer is None:
            context = ConfigurationContext.from_workspace(
                workspace, project_config_path=runner.runtime_config_path,
            )
            name = args.reward_model or runner.root_config.model_config_name
            # Agent declarations use "default" as a workspace-policy selector,
            # not a catalog entry. Follow the same runtime setting as generation.
            if not args.reward_model and name == DEFAULT_MODEL_CONFIG_NAME:
                runtime = context.read_merged_config().get("runtime", {})
                name = (
                    str(runtime.get("model_name") or DEFAULT_RUNTIME_MODEL_NAME).strip()
                    or DEFAULT_RUNTIME_MODEL_NAME
                )
            catalog = list_runtime_models(
                runtime_config_path=context.project_config_path, workspace_dir=workspace,
            )
            if name not in {model["model_name"] for model in catalog}:
                raise ValueError(f"奖励模型不在当前示例模型目录中: {name}")
            scorer = GenerativeRewardModel.from_config(name, context)
            print(f"奖励模型: {name}", flush=True)
        strategy = (
            DemonstrationStrategy(breadth=args.breadth, reward_model=scorer)
            if args.strategy == "tot" else GreedyStrategy()
        )
        if args.strategy == "tot" and args.breadth > 1:
            print(f"每步候选选择: breadth={args.breadth}，评分后择一执行", flush=True)
        else:
            print("线性执行：greedy 或 breadth=1，每步仅请求一次生成模型。", flush=True)
        # Runtime-only dependencies are reattached on both create and resume.
        runner._root().instance.set_exploration_strategy(strategy)

    return run_showcase(
        args,
        agent_mode="agent",
        workspace=workspace,
        task=args.task,
        configure_runner=configure_runner,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
