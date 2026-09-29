"""轨迹导出器：把持久化的 runner 会话导出为 post-training 数据集。

数据来源是 ``.juice/runners/<runner_id>/agents/<agent_id>/`` 下由
``AgentManager`` 持久化的 ``manifest.json`` + ``session.json``。导出器复用
``JsonAgentSnapshotStore`` 读取这份 Manager-owned 快照，复用 sessions 层的
``deserialize_session`` 反序列化，不重复实现磁盘格式解析。

导出以 JSONL 流式写出（每行一条样本），避免把整个数据集读进内存。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from juice_agents.core.agent.manager import JsonAgentSnapshotStore
from juice_agents.core.agent.sessions import AgentSession, deserialize_session
from juice_agents.core.runner.persistence import (
    build_layout,
    load_runner_state,
)

from .filters import ExportConfig, should_export_session
from .formats import session_to_sample

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExportResult:
    """一次导出的统计结果。"""

    output_path: str
    num_samples: int = 0          # 实际写出的样本数
    num_messages: int = 0         # 所有样本的消息总数
    num_sessions_scanned: int = 0  # 扫描过的会话数（含被过滤的）
    num_sessions_skipped: int = 0  # 被会话级过滤丢弃的会话数
    runner_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "num_samples": self.num_samples,
            "num_messages": self.num_messages,
            "num_sessions_scanned": self.num_sessions_scanned,
            "num_sessions_skipped": self.num_sessions_skipped,
            "runner_ids": list(self.runner_ids),
        }


@dataclass(slots=True)
class _AgentSession:
    """一个 managed agent 的会话及其导出元数据。"""

    session: AgentSession
    metadata: dict[str, Any]


class TrajectoryExporter:
    """把 runner 持久化轨迹导出为 post-training 数据集。

    用法：

    ```python
    exporter = TrajectoryExporter(base_dir=".")
    result = exporter.export_runner("11a94b3a", "out.jsonl")
    result = exporter.export_workspace("data.jsonl", config={"success_only": True})
    ```
    """

    def __init__(self, base_dir: str | Path = "."):
        self.base_dir = Path(base_dir).expanduser().resolve()

    # ------------------------------------------------------------------ #
    # 公共导出入口
    # ------------------------------------------------------------------ #

    def export_runner(
        self,
        runner_id: str,
        output_path: str | Path,
        *,
        config: ExportConfig | dict[str, Any] | None = None,
    ) -> ExportResult:
        """导出单个 runner 的所有 managed agent 会话。"""
        return self._export([runner_id], output_path, config)

    def export_multiple_runners(
        self,
        runner_ids: list[str],
        output_path: str | Path,
        *,
        config: ExportConfig | dict[str, Any] | None = None,
    ) -> ExportResult:
        """批量导出多个 runner 的会话到同一个 JSONL 文件。"""
        return self._export(list(runner_ids), output_path, config)

    def export_workspace(
        self,
        output_path: str | Path,
        *,
        config: ExportConfig | dict[str, Any] | None = None,
    ) -> ExportResult:
        """导出 workspace 下 ``.juice/runners/`` 的所有 runner。"""
        return self._export(self._discover_runner_ids(), output_path, config)

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #

    def _normalize_config(self, config: ExportConfig | dict[str, Any] | None) -> ExportConfig:
        if isinstance(config, ExportConfig):
            return config
        return ExportConfig.from_dict(config)

    def _discover_runner_ids(self) -> list[str]:
        """扫描 ``.juice/runners/`` 下的所有 runner 目录。"""
        runners_dir = self.base_dir / ".juice" / "runners"
        if not runners_dir.is_dir():
            logger.warning("未找到 runners 目录: %s", runners_dir)
            return []
        return sorted(
            child.name
            for child in runners_dir.iterdir()
            if child.is_dir() and (child / "manifest.json").is_file()
        )

    def _iter_agent_sessions(self, runner_id: str) -> Iterator[_AgentSession]:
        """读取单个 runner 下所有 Manager-owned agent 快照。"""
        layout = build_layout(self.base_dir, runner_id)
        if not layout.manifest_path.is_file():
            logger.warning("runner %s 不存在或缺少 manifest，跳过", runner_id)
            return

        try:
            runner_state = load_runner_state(layout.manifest_path)
        except Exception as exc:  # 损坏的 manifest 不应中断整体导出
            logger.warning("读取 runner %s manifest 失败: %s", runner_id, exc)
            runner_state = {}

        # ``JsonAgentSnapshotStore`` is the canonical runtime persistence
        # boundary.  Reading its projection guarantees export stays aligned
        # with AgentManager schema changes instead of recreating an old
        # legacy runtime-directory parser here.
        for agent_snapshot in JsonAgentSnapshotStore(layout.agents_dir).list():
            session_payload = agent_snapshot.get("session") or {}
            if not session_payload.get("steps"):
                # 没有任何 step 的空会话不值得导出。
                continue

            session = deserialize_session(session_payload)
            metadata = self._build_metadata(runner_id, runner_state, agent_snapshot)
            yield _AgentSession(session=session, metadata=metadata)

    def _build_metadata(
        self,
        runner_id: str,
        runner_state: dict[str, Any],
        agent_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """构造样本元数据，便于训练侧按来源筛选与溯源。"""
        agent_metadata = dict(agent_snapshot.get("metadata") or {})
        mode_id = str(
            agent_metadata.get("mode_id")
            or runner_state.get("agent_mode")
            or "agent"
        )
        return {
            "runner_id": runner_id,
            "agent_id": str(agent_snapshot.get("agent_id") or ""),
            "agent_name": str(agent_snapshot.get("agent_name") or ""),
            "agent_role": str(agent_snapshot.get("role") or ""),
            "agent_mode": mode_id,
            "mode_id": mode_id,
            "is_root": bool(agent_snapshot.get("is_root")),
            "created_at": runner_state.get("created_at"),
        }

    def _export(
        self,
        runner_ids: list[str],
        output_path: str | Path,
        config: ExportConfig | dict[str, Any] | None,
    ) -> ExportResult:
        """核心导出循环：遍历 runner -> agent -> 会话，流式写 JSONL。"""
        resolved_config = self._normalize_config(config)
        out_path = Path(output_path).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        result = ExportResult(output_path=str(out_path))

        with out_path.open("w", encoding="utf-8") as handle:
            for runner_id in runner_ids:
                exported_for_runner = False
                for agent_session in self._iter_agent_sessions(runner_id):
                    result.num_sessions_scanned += 1

                    if not should_export_session(agent_session.session, resolved_config):
                        result.num_sessions_skipped += 1
                        continue

                    sample = session_to_sample(
                        agent_session.session,
                        resolved_config,
                        agent_session.metadata,
                    )
                    if sample is None:
                        result.num_sessions_skipped += 1
                        continue

                    handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
                    result.num_samples += 1
                    result.num_messages += len(sample["messages"])
                    exported_for_runner = True

                if exported_for_runner:
                    result.runner_ids.append(runner_id)

        logger.info(
            "导出完成: %s 条样本 / %s 条消息 -> %s",
            result.num_samples,
            result.num_messages,
            out_path,
        )
        return result


__all__ = [
    "ExportResult",
    "TrajectoryExporter",
]
