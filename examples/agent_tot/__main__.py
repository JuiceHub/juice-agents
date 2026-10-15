"""演示如何使用探索策略（ToT/Beam Search/Best-of-N）。

本示例展示：
1. 默认贪心策略（单路径）
2. Best-of-N 策略（生成多个候选，选最优）
3. Beam Search 策略（保持多条路径并行）
4. ToT 策略（单路径深度探索）
"""

from __future__ import annotations

import argparse

from .._runtime import add_runtime_arguments, run_showcase, showcase_workspace


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="python -m examples.agent_tot",
        description=(
            "演示探索策略的使用。支持 greedy/best_of_n/beam_search/tot 等不同策略。"
        )
    )
    parser.add_argument(
        "task",
        help="要执行的任务，例如：'优化这段代码的性能'",
    )
    parser.add_argument(
        "--strategy",
        choices=["greedy", "best_of_n", "beam_search", "tot"],
        default="greedy",
        help="探索策略类型 (默认: greedy)",
    )
    parser.add_argument(
        "--breadth",
        type=int,
        default=3,
        help="每步展开的分支数 (默认: 3)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=3,
        help="最大探索深度 (默认: 3)",
    )
    parser.add_argument(
        "--beam-width",
        type=int,
        default=2,
        help="每步保留的路径数 (默认: 2)",
    )
    add_runtime_arguments(parser)
    return parser


def main(argv: list[str] | None = None):
    """运行探索策略示例。"""
    args = build_parser().parse_args(argv)

    # 根据策略类型配置 agent
    from juice_agents.core.agent.context.strategies import (
        GreedyStrategy,
        TreeOfThoughtsStrategy,
    )

    if args.strategy == "greedy":
        # 贪心策略：单路径，确定性
        strategy = GreedyStrategy()
        print(f"\n使用策略: Greedy (单路径)")
    elif args.strategy == "best_of_n":
        # Best-of-N: 生成 N 个候选，选最优
        strategy = TreeOfThoughtsStrategy(
            breadth=args.breadth,
            depth_limit=1,
            keep_top_k=1
        )
        print(f"\n使用策略: Best-of-N (breadth={args.breadth})")
    elif args.strategy == "beam_search":
        # Beam Search: 保持固定宽度的并行路径
        strategy = TreeOfThoughtsStrategy(
            breadth=args.breadth,
            depth_limit=args.depth,
            keep_top_k=args.beam_width
        )
        print(f"\n使用策略: Beam Search (breadth={args.breadth}, depth={args.depth}, beam_width={args.beam_width})")
    else:  # tot
        # ToT: 单路径深度探索，每步探索多个分支
        strategy = TreeOfThoughtsStrategy(
            breadth=args.breadth,
            depth_limit=args.depth,
            keep_top_k=1
        )
        print(f"\n使用策略: ToT (breadth={args.breadth}, depth={args.depth})")

    print(f"任务: {args.task}\n")

    # 注意：这里演示如何设置策略
    # 实际运行时，策略会在 agent 创建后通过 set_exploration_strategy() 设置
    # 或在 CLI 中通过 /strategy 命令动态切换

    return run_showcase(
        args,
        agent_mode="agent",
        workspace=showcase_workspace(__file__),
        task=args.task,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
