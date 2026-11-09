"""异步运行时与同步扩展之间的受控边界。

文件事务和用户提供的同步工具不能在事件循环中执行。取消等待者后仍等待
已提交的工作完成，避免出现“已报告取消，后台却仍在写会话”的状态。
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

T = TypeVar("T")

# 同步生成器会在 yield 处等待消费者确认，不能占满执行短文件事务的默认池。
# 独立有界池保证大量 Agent 同时执行工具时，候选落盘/事件回调仍能前进。
_streams = ThreadPoolExecutor(max_workers=8, thread_name_prefix="juice-sync-stream")
_stream_stops: set[tuple[threading.Event, threading.Event]] = set()
_stream_lock = threading.Lock()


def _stop_streams() -> None:
    with _stream_lock:
        for stopped, consumed in _stream_stops:
            stopped.set()
            consumed.set()


threading._register_atexit(_stop_streams)


async def cancel_and_drain(task: asyncio.Task) -> None:
    """只发一次取消，等待 finally；不消费异常，以便所有者保留原始用量。

    父取消与业务令牌可能同时到达。再次 task.cancel() 会打断已经开始的
    异步清理，因此只取消尚未进入取消状态的任务，等待过程也屏蔽父重复取消。
    """
    if not task.done() and not task.cancelling():
        task.cancel()
    if task.done():
        return
    waiter = asyncio.create_task(asyncio.wait((task,)))
    while not waiter.done():
        try:
            await asyncio.shield(waiter)
        except asyncio.CancelledError:
            continue


async def invoke(call: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """扩展接口既可提供协程，也可提供同步函数，阻塞代码始终离开循环。"""
    result = call(*args, **kwargs) if inspect.iscoroutinefunction(call) else await blocking(call, *args, **kwargs)
    return await result if inspect.isawaitable(result) else result


async def blocking(call: Callable[..., T], *args: Any,
                   on_cancel: Callable[[], Any] | None = None,
                   on_discard: Callable[[T], Any] | None = None,
                   executor: ThreadPoolExecutor | None = None, **kwargs: Any) -> T:
    """在线程执行完整同步操作；取消只能发信号，不能遗弃运行中的线程。"""
    worker = (asyncio.ensure_future(asyncio.get_running_loop().run_in_executor(executor, partial(call, *args, **kwargs)))
              if executor is not None else asyncio.create_task(asyncio.to_thread(partial(call, *args, **kwargs))))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError:
            cancelled = True
            if on_cancel is not None:
                on_cancel()
            if worker.done():
                # 检索异常，确保工作线程的失败不会变成未处理 Task 异常。
                result = worker.result()
                break
    if cancelled:
        if on_discard is not None:
            await invoke(on_discard, result)
        raise asyncio.CancelledError
    return result


async def iterate_blocking(factory: Callable[[], Iterator[T]], *,
                           on_cancel: Callable[[], Any] | None = None,
                           result: list[Any] | None = None) -> AsyncIterator[T]:
    """一个工作线程消费整个同步生成器，保证线程亲和性及 finally 收尾。"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[bool, Any]] = asyncio.Queue()
    stopped = threading.Event()
    consumed = threading.Event()
    with _stream_lock:
        _stream_stops.add((stopped, consumed))

    def consume() -> None:
        iterator = factory()
        try:
            while True:
                try:
                    value = next(iterator)
                except StopIteration as end:
                    if result is not None:
                        result.append(end.value)
                    break
                if stopped.is_set():
                    break
                consumed.clear()
                loop.call_soon_threadsafe(queue.put_nowait, (True, value))
                # 下一次 next 必须等消费者处理完当前事件；特别是候选必须先落盘，
                # 才允许同步自定义 Agent 继续产生工具副作用。
                consumed.wait()
                if stopped.is_set():
                    break
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()

    async def produce() -> None:
        try:
            await blocking(consume, on_cancel=on_cancel, executor=_streams)
        except BaseException as exc:
            await queue.put((False, exc))
        else:
            await queue.put((False, None))

    producer = asyncio.create_task(produce())
    try:
        while True:
            is_value, value = await queue.get()
            if is_value:
                try:
                    yield value
                finally:
                    consumed.set()
            else:
                if value is not None:
                    raise value
                return
    except asyncio.CancelledError:
        if on_cancel is not None:
            on_cancel()
        raise
    finally:
        stopped.set()
        consumed.set()
        # 不取消 producer；它必须消费完受控同步工作并关闭生成器。
        await asyncio.shield(producer)
        with _stream_lock:
            _stream_stops.discard((stopped, consumed))
