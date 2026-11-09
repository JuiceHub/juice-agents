"""模型调用的运行时所有者：请求、取消、事件、账本和连接释放。"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4

from ..utils import normalize_token_usage
from ..runner.execution.cancellation import StreamCancelled, raise_if_cancelled
from ..runner.execution.async_bridge import run_async
from ..runner.execution.async_io import blocking, cancel_and_drain

logger = logging.getLogger(__name__)

async def invoke_model_call(
    model: Any, method: str, *, purpose: Literal["generation", "scoring", "compression"],
    args: tuple[Any, ...] = (), kwargs: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
    on_model_usage: Callable[..., None] | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> Any:
    """一个逻辑请求对应一对事件；身份及用量均留在请求栈上。"""
    raise_if_cancelled(cancel_event)
    call = getattr(model, method)
    options = dict(kwargs or {})
    try:
        parameters = inspect.signature(call).parameters.values()
    except (ValueError, TypeError):
        parameters = ()
    if cancel_event is not None:
        if any(p.name == "cancel_event" or p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters):
            options["cancel_event"] = cancel_event
        if inspect.iscoroutinefunction(call) and any(p.name == "stream" for p in parameters):
            options["stream"] = True
    identity = {"model_call_id": uuid4().hex, "purpose": purpose,
                "backend": str(getattr(model, "backend", "") or ""),
                "model_name": str(getattr(model, "model_name", "") or "")}

    async def emit(kind: str, data: dict[str, Any]) -> None:
        if on_event is not None:
            await blocking(on_event, kind, data)

    async def report(**result: Any) -> None:
        if on_model_usage is not None:
            await blocking(on_model_usage, kind="reward" if purpose == "scoring" else "generation",
                           model=model, **result)

    async def request() -> Any:
        # 内置模型直接 await；同步自定义模型是明确的扩展边界。
        response = (call(*args, **options) if inspect.iscoroutinefunction(call)
                    else await blocking(call, *args, on_cancel=cancel_event.set if cancel_event else None, **options))
        return await response if inspect.isawaitable(response) else response

    await emit("model_start", dict(identity))
    started, response, failure = time.monotonic(), None, None
    operation = asyncio.create_task(request())
    try:
        try:
            if cancel_event is not None:
                while not operation.done() and not cancel_event.is_set():
                    await asyncio.wait((operation,), timeout=0.02)
                if cancel_event.is_set():
                    await cancel_and_drain(operation)
            response = await asyncio.shield(operation)
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError):
                await cancel_and_drain(operation)
                if operation.cancelled() and not hasattr(exc, "usage_calls"):
                    try:
                        operation.result()
                    except asyncio.CancelledError as original:
                        # shield 的取消异常不含请求明细；保留模型原始异常。
                        exc = original
                elif not operation.cancelled():
                    error = operation.exception()
                    response = operation.result() if error is None else None
                    exc.usage_calls = model_usage_calls(model, response, error)
            await report(error=exc)
            if cancel_event is not None and cancel_event.is_set() and not isinstance(exc, StreamCancelled):
                cancelled = StreamCancelled()
                cancelled.usage_calls = getattr(exc, "usage_calls", [])
                raise cancelled from exc
            raise exc
        await report(response=response)
        raise_if_cancelled(cancel_event)
        return response
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if not operation.done():
            await cancel_and_drain(operation)
            await asyncio.gather(operation, return_exceptions=True)
        await emit("model_end", {**identity,
            "status": "cancelled" if isinstance(failure, (StreamCancelled, asyncio.CancelledError)) else "failed" if failure else "completed",
            "error": str(failure) if failure else None,
            "usage": normalize_token_usage(response.get("usage")) if isinstance(response, dict) else None,
            "execution_time": time.monotonic() - started})


class ModelManager:
    """一个运行时拥有一个账本，显式等待全部请求和连接释放。"""

    def __init__(self, *, usage_path: str | Path | None = None) -> None:
        self._ledger = ModelUsageLedger(None if usage_path is None else Path(usage_path))
        self._models: dict[int, Any] = {}
        self._requests: set[asyncio.Task] = set()
        self._closed = False

    async def acall(self, model: Any, method: str, **kwargs: Any) -> Any:
        if self._closed:
            raise RuntimeError("ModelManager 已关闭")
        task = asyncio.current_task()
        self._requests.add(task)
        # 显式管理框架创建的客户端；外部注入的客户端保持调用方所有权。
        if getattr(model, "_owns_client", False) is True:
            self._models[id(model)] = model
        try:
            return await invoke_model_call(model, method, **kwargs)
        finally:
            self._requests.discard(task)

    def call(self, model: Any, method: str, **kwargs: Any) -> Any:
        return run_async(self.acall(model, method, **kwargs))

    @staticmethod
    def invoke_sync(model: Any, method: str, **kwargs: Any) -> Any:
        # 独立同步扩展没有 Runner 所有权，仍交由桥接层兜底回收连接。
        return run_async(invoke_model_call(model, method, **kwargs), owner=model)

    def record(self, *, agent_id: str, agent_name: str, kind: str, model: Any,
               response: Any = None, error: BaseException | None = None) -> None:
        for call in model_usage_calls(model, response, error):
            self._ledger.append(agent_id=agent_id, agent_name=agent_name, kind=kind, call=call)

    def model_usage_records(self) -> list[dict[str, Any]]:
        return self._ledger.records()

    def token_usage(self) -> dict[str, Any]:
        return self._ledger.summary()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        pending = self._requests - {asyncio.current_task()}
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        models, self._models = tuple(self._models.values()), {}
        results = await asyncio.gather(*(model.aclose() for model in models), return_exceptions=True)
        failures = [result for result in results if isinstance(result, Exception)]
        if failures:
            logger.error("model_manager_close_failed count=%s", len(failures))
            raise ExceptionGroup("模型连接释放失败", failures)

    def close(self) -> None:
        run_async(self.aclose())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()


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
