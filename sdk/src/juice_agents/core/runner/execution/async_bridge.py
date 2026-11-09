"""SDK 与持久执行循环的调用边界，不包装模型或供应商 SDK。

同步及外部异步宿主各适配一次完整操作，运行时内部直接 await。
取消令牌发出后等待协程 finally 收尾，再向宿主交回控制。
"""

from __future__ import annotations

import asyncio
import atexit
from collections.abc import AsyncIterator, Coroutine, Iterator
import logging
import threading
from typing import Any, TypeVar
import weakref

from .cancellation import StreamCancelled

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


class _Runtime:
    def __init__(self) -> None:
        self.clients: dict[int, Any] = {}
        self.owners: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        ready = threading.Event()

        def serve() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            ready.set()
            self.loop.run_forever()
            self.loop.close()

        self.thread = threading.Thread(target=serve, name="runner-async-bridge", daemon=True)
        self.thread.start()
        ready.wait()

    async def close_client(self, client: Any) -> None:
        if self.clients.pop(id(client), None) is not None:
            await client.close()

    def track(self, owner: Any) -> None:
        """只管理内置模型自己创建的连接；外部注入的客户端仍归调用方。"""
        client = getattr(owner, "_client", None)
        if getattr(owner, "_owns_client", False) is not True:
            return
        previous = self.owners.get(owner)
        closed = getattr(owner, "_closed", False)
        if previous is not None and previous[0] is client and not closed:
            return
        if previous is not None:
            # aclose/reload_config 已经在所属循环关闭旧连接，移除兜底回收记录。
            previous[1].detach()
            self.clients.pop(id(previous[0]), None)
            del self.owners[owner]
        if client is None or closed:
            return
        self.clients[id(client)] = client
        finalizer = weakref.finalize(owner, self.finalize_client, client)
        self.owners[owner] = (client, finalizer)

    def finalize_client(self, client: Any) -> None:
        if self.thread.is_alive():
            asyncio.run_coroutine_threadsafe(self.close_client(client), self.loop)

    async def execute(
        self, operation: Coroutine[Any, Any, _T],
        cancel_event: threading.Event | None, owner: Any,
    ) -> _T:
        task = asyncio.create_task(operation)
        try:
            # 一个调用只监视一次取消令牌，不为每个 SDK await/流分片创建桥接。
            if cancel_event is not None:
                while not task.done() and not cancel_event.is_set():
                    await asyncio.wait((task,), timeout=0.02)
                if cancel_event.is_set():
                    task.cancel()
                    usage_calls = []
                    try:
                        result = await task
                        if isinstance(result, dict):
                            usage_calls = result.get("usage_calls", [])
                    except (Exception, asyncio.CancelledError) as exc:
                        usage_calls = getattr(exc, "usage_calls", [])
                    cancelled = StreamCancelled()
                    cancelled.usage_calls = usage_calls
                    logger.debug("模型协程已完成取消清理")
                    raise cancelled
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.track(owner)

    async def close(self) -> None:
        # 先取消任务并等待 finally，再关闭连接池；不让请求与关池并发。
        pending = asyncio.all_tasks() - {asyncio.current_task()}
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*(self.close_client(c) for c in tuple(self.clients.values())),
                             return_exceptions=True)


_runtime: _Runtime | None = None
_lock = threading.Lock()


def runtime() -> _Runtime:
    """同步与异步 Runner 共享一个持久循环，连接池不会跨循环漂移。"""
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = _Runtime()
        return _runtime


async def on_runtime(operation: Coroutine[Any, Any, _T]) -> _T:
    """异步宿主的入口适配；运行时内部调用直接 await，不再跨线程。"""
    host = runtime()
    if asyncio.get_running_loop() is host.loop:
        return await operation
    # wrap_future 默认会把外部取消立即传给 concurrent Future；该 Future 会在
    # finally 完成前显示 done。用协作令牌请求取消，显式等内部清理完成后再返回。
    cancel_event = threading.Event()
    future = asyncio.run_coroutine_threadsafe(host.execute(operation, cancel_event, None), host.loop)
    completion = asyncio.wrap_future(future)
    try:
        return await asyncio.shield(completion)
    except asyncio.CancelledError:
        cancel_event.set()
        # 宿主可能重复 cancel（例如先用户取消、再超时）；仍须等内部 finally。
        while not completion.done():
            try:
                await asyncio.shield(completion)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not completion.cancelled():
            completion.exception()
        raise


async def iter_async(iterator: AsyncIterator[_T]) -> AsyncIterator[_T]:
    """异步观察者可以在自己的循环消费，生成器始终留在运行时循环。"""
    try:
        while True:
            try:
                value = await on_runtime(anext(iterator))
            except StopAsyncIteration:
                return
            yield value
    finally:
        await on_runtime(iterator.aclose())


def iter_sync(iterator: AsyncIterator[_T]) -> Iterator[_T]:
    """同步流仅适配异步生成器，不拥有另一个执行器或事件循环。"""
    try:
        while True:
            try:
                value = run_async(anext(iterator))
            except StopAsyncIteration:
                return
            yield value
    finally:
        run_async(iterator.aclose())


def run_async(
    operation: Coroutine[Any, Any, _T], *,
    cancel_event: threading.Event | None = None, owner: Any = None,
) -> _T:
    """同步等待一个完整协程；不支持从任何事件循环线程中阻塞调用。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        operation.close()
        raise RuntimeError("异步调用方应直接 await 模型；同步 Runner 请在线程中调用")
    host = runtime()
    return asyncio.run_coroutine_threadsafe(
        host.execute(operation, cancel_event, owner), host.loop,
    ).result()


def _shutdown() -> None:
    if _runtime is None or not _runtime.thread.is_alive():
        return
    try:
        asyncio.run_coroutine_threadsafe(_runtime.close(), _runtime.loop).result(timeout=3)
    except Exception:
        logger.warning("Runner 异步连接清理未完成", exc_info=True)
    finally:
        _runtime.loop.call_soon_threadsafe(_runtime.loop.stop)
        _runtime.thread.join(timeout=1)


atexit.register(_shutdown)
