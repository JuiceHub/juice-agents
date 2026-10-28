"""AgentManager 独占的模型请求账本；不依赖会话树或模型实例状态。"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..utils import normalize_token_usage


class ModelUsageLedger:
    """逐请求追加 JSONL，读取与汇总不会再次记录或唤醒 Agent。

    正常 Manager 使用 agents/model_usage.jsonl；自定义无磁盘 snapshot store
    可使用内存账本。账本只含模型/Agent 标识及计数，不保存提示词或响应正文。
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._records: list[dict[str, Any]] = []
        if path is not None and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError(f"模型用量记录必须为 object: {path}")
                    self._records.append(record)

    def append(self, *, agent_id: str, agent_name: str, kind: str, call: dict[str, Any]) -> None:
        if kind not in {"generation", "reward"}:
            raise ValueError(f"未知模型用量用途: {kind}")
        record = {
            "request_id": uuid4().hex,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "kind": kind,
            "backend": str(call.get("backend") or ""),
            "model_name": str(call.get("model_name") or ""),
            "usage": normalize_token_usage(call.get("usage")),
        }
        with self._lock:
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            self._records.append(record)

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._records)

    def summary(self) -> dict[str, dict[str, int]]:
        """只累加已报告的计数，缺任一标准字段的请求明确标为不完整。"""
        fields = ("input_tokens", "output_tokens", "total_tokens")
        totals = {
            kind: {"requests": 0, **dict.fromkeys(fields, 0), "missing_usage_requests": 0}
            for kind in ("generation", "reward", "total")
        }
        for record in self.records():
            kind = record["kind"]
            usage = normalize_token_usage(record.get("usage")) or {}
            for bucket in (totals[kind], totals["total"]):
                bucket["requests"] += 1
                bucket["missing_usage_requests"] += int(any(field not in usage for field in fields))
                for field in fields:
                    bucket[field] += usage.get(field, 0)
        return totals


def model_usage_calls(model: Any, response: Any = None, error: Exception | None = None) -> list[dict[str, Any]]:
    """提取模型返回的请求明细；支持只返回单份 usage 的自定义模型。

    显式空明细表示没有实际请求（例如 GRM 空批次），不能补出虚构记录。
    无明细的异常也不推断是否已请求；内置模型会附带已执行的请求明细。
    """
    source = error if error is not None else response
    if hasattr(source, "usage_calls") or isinstance(source, dict) and "usage_calls" in source:
        calls = source.get("usage_calls") if isinstance(source, dict) else source.usage_calls
        return list(calls) if isinstance(calls, list) else []
    if error is not None or not isinstance(response, dict):
        return []
    return [{
        "backend": getattr(model, "backend", ""),
        "model_name": getattr(model, "model_name", ""),
        "usage": response.get("usage"),
    }]
