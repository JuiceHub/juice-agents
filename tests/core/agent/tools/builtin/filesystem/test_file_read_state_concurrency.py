"""不同文件并行访问时，共享 read-state 的原子更新契约。"""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from juice_agents.core.agent.tools.builtin.filesystem import files_tools
from juice_agents.core.managers.tools import ToolManager


class _ObservedLock:
    """报告确实发生的锁竞争，测试无需通过 sleep 或执行耗时推断等待。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.contended = threading.Event()

    def __enter__(self):
        if not self._lock.acquire(blocking=False):
            self.contended.set()
            self._lock.acquire()
        return self

    def __exit__(self, *args):
        self._lock.release()


def _snapshot(path, content):
    return files_tools._ReadSnapshot(
        resolved_path=path, mtime_ns=0, is_partial=False, read_at="",
        content=content, size_bytes=len(content.encode()),
    )


@pytest.mark.parametrize("operation", ["evict", "reset"])
def test_snapshot_lookup_is_atomic_with_eviction_and_reset(operation):
    reading = threading.Event()
    finish_reading = threading.Event()

    class PausedSnapshots(OrderedDict):
        def move_to_end(self, key, last=True):
            # 暂停在「确认键存在」之后，重现原先容易被淘汰/清空打断的窗口。
            reading.set()
            assert finish_reading.wait(3)
            return super().move_to_end(key, last=last)

    original = _snapshot("old", "original")
    replacement = _snapshot("new", "replacement")
    state = files_tools._FileRuntimeState(max_entries=1)
    state.add_snapshot("old", original)
    state.snapshots = PausedSnapshots(state.snapshots)
    lock = _ObservedLock()
    state._lock = lock
    with ThreadPoolExecutor(max_workers=2) as pool:
        lookup = pool.submit(state.get_snapshot, "old")
        try:
            assert reading.wait(3)
            mutation = pool.submit(
                state.add_snapshot, "new", replacement,
            ) if operation == "evict" else pool.submit(state.reset)
            assert lock.contended.wait(3), "cache mutation did not wait for the in-progress lookup"
            assert not mutation.done()
        finally:
            finish_reading.set()
        assert lookup.result(timeout=3) is original
        mutation.result(timeout=3)

    assert "old" not in state.snapshots
    if operation == "evict":
        assert state.get_snapshot("new") is replacement
        assert state.current_size_bytes == replacement.size_bytes
    else:
        assert not state.snapshots
        assert state.current_size_bytes == 0


def test_first_parallel_calls_share_one_runtime_state(tmp_path, monkeypatch):
    create_started = threading.Event()
    finish_creation = threading.Event()
    lock = _ObservedLock()
    created = []
    state_type = files_tools._FileRuntimeState

    def create_state():
        state = state_type()
        created.append(state)
        create_started.set()
        assert finish_creation.wait(3)
        return state

    # 用全新的共享注册表复现同 root 下两个不同工具的第一次访问。
    monkeypatch.setattr(files_tools._BaseFileTool, "_STATE_BY_KEY", {})
    monkeypatch.setattr(files_tools._BaseFileTool, "_STATE_BY_KEY_LOCK", lock)
    monkeypatch.setattr(files_tools, "_FileRuntimeState", create_state)
    reader = files_tools.ReadTool(root_dir=tmp_path)
    writer = files_tools.WriteTool(root_dir=tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(reader._get_runtime_state)
        try:
            assert create_started.wait(3)
            second = pool.submit(writer._get_runtime_state)
            assert lock.contended.wait(3), "second caller did not wait for state publication"
        finally:
            finish_creation.set()
        assert first.result(timeout=3) is second.result(timeout=3)
    assert len(created) == 1


def test_parallel_file_reads_keep_lru_bounds_without_serializing_io(tmp_path, monkeypatch):
    reader = files_tools.ReadTool(root_dir=tmp_path)
    state = reader._get_runtime_state()
    state.max_entries = 2
    rendezvous = threading.Barrier(4, timeout=3)
    read_text = reader._read_text_file

    def concurrent_read(target, *, encoding):
        # 四个不同文件必须同时进入 I/O，缓存锁不能扩大为整次读取的锁。
        rendezvous.wait()
        return read_text(target, encoding=encoding)

    monkeypatch.setattr(reader, "_read_text_file", concurrent_read)
    paths = [f"{index}.txt" for index in range(4)]
    for path in paths:
        (tmp_path / path).write_text(f"content for {path}")
    manager = ToolManager([reader], max_workers=4)
    try:
        results = manager.execute([{"name": "read", "args": {"path": path}} for path in paths])
        assert all(result.status == "completed" for result in results)
        assert [result.observation["content"] for result in results] == [f"content for {p}" for p in paths]
        assert len(state.snapshots) == 2
        assert state.current_size_bytes == sum(snapshot.size_bytes for snapshot in state.snapshots.values())
    finally:
        rendezvous.abort()
        manager.release()
