"""Pytest bootstrap for repository-wide test isolation."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tests.support.temp_paths import (
    TEST_TEMP_ROOT,
    cleanup_test_temp_root,
    configure_test_temp_root,
    get_test_temp_root,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SDK_SRC_ROOT = REPO_ROOT / "sdk" / "src"

# ``juice_agents`` uses the standard ``src/`` package layout.  Add that source
# root before test modules are imported so repository tests exercise the SDK
# package exactly as development launchers do.
if str(SDK_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_SRC_ROOT))

# 在 conftest 加载阶段就把 tempfile 重定向到 TEST_TEMP_ROOT，
# 避免任何模块级 fixture 在 fixture 启动前就尝试落盘。
configure_test_temp_root()


@pytest.fixture
def test_temp_root() -> Path:
    """Expose the shared test temp root to pytest-style tests."""

    return get_test_temp_root()


@pytest.fixture(autouse=True, scope="session")
def _isolate_repo_writes():
    """阻断 SDK 默认落盘到仓库根 / cwd。

    覆盖两类已知泄漏点：
    - ``Path.cwd()`` 兜底（``Runner.create()``、``RuntimeWorkspace.juice_root``、
      ``async_tasks/registry.py`` 等）：通过把 cwd 切到 ``TEST_TEMP_ROOT`` 阻断。
    - ``ConfigurationContext.from_workspace(None)`` now resolves writable state
      from cwd, so the isolated cwd below is its only required boundary.
    - ``DEFAULT_OBSERVATION_IMAGE_CACHE_DIR`` 是相对路径，cwd 切走后仍写到隔离根。
      仍同时把它指向假根，让相对/绝对两条路径都安全。
    """
    import juice_agents.core.agent.agent_type as agent_type

    fake_repo_root = TEST_TEMP_ROOT / "_fake_repo_root"
    fake_repo_root.mkdir(parents=True, exist_ok=True)

    saved_obs_cache = agent_type.DEFAULT_OBSERVATION_IMAGE_CACHE_DIR
    saved_cwd = Path.cwd()
    saved_pythonpath = os.environ.get("PYTHONPATH")

    agent_type.DEFAULT_OBSERVATION_IMAGE_CACHE_DIR = str(
        fake_repo_root / ".juice" / "observation_images"
    )
    # 子进程从隔离 cwd 启动时同时需要仓库内 adapter 与 ``src/`` 布局 SDK。
    python_paths = [str(SDK_SRC_ROOT), str(REPO_ROOT)]
    if saved_pythonpath:
        python_paths.append(saved_pythonpath)
    os.environ["PYTHONPATH"] = os.pathsep.join(python_paths)
    os.chdir(TEST_TEMP_ROOT)

    try:
        yield
    finally:
        # 即便测试中途切了 cwd，也要保证恢复到原始 cwd，不影响后续工具。
        os.chdir(saved_cwd)
        agent_type.DEFAULT_OBSERVATION_IMAGE_CACHE_DIR = saved_obs_cache
        if saved_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = saved_pythonpath


# 仓库根残留探针：测试结束后这些条目不应该新增。
_LEAK_PROBES = (".juice", "MagicMock")

# SDK 包内目录属于只读包数据（wheel 安装后位于 site-packages）。任何运行时状态
# 写到这里都是 bug：曾出现过 workspace_dir 为空时回退到 runtime_config_path 的父
# 目录，把 team 配置写进 `_assets/.juice/`。仓库根探针覆盖不到这一层，故单列。
_READ_ONLY_PACKAGE_DIRS = (SDK_SRC_ROOT / "juice_agents" / "_assets",)


def _snapshot_repo_leaks() -> set[str]:
    leaks: set[str] = set()
    if REPO_ROOT.exists():
        leaks |= {entry.name for entry in REPO_ROOT.iterdir() if entry.name in _LEAK_PROBES}
    for package_dir in _READ_ONLY_PACKAGE_DIRS:
        if not package_dir.exists():
            continue
        leaks |= {
            f"{package_dir.relative_to(REPO_ROOT)}/{entry.name}"
            for entry in package_dir.iterdir()
            if entry.name in _LEAK_PROBES
        }
    return leaks


_pre_session_leaks: set[str] = set()


def pytest_sessionstart(session):
    """记录测试开始前已存在的探针条目，避免误报历史残留。"""
    global _pre_session_leaks
    _pre_session_leaks = _snapshot_repo_leaks()


def pytest_sessionfinish(session, exitstatus):
    """清理本进程临时目录，并断言仓库根没有新增写入。"""
    cleanup_test_temp_root()

    new_leaks = _snapshot_repo_leaks() - _pre_session_leaks
    if new_leaks:
        sys.stderr.write(
            f"\n[conftest] 测试在仓库根遗留了文件/目录: {sorted(new_leaks)}；"
            "检查 conftest._isolate_repo_writes 是否覆盖到所有泄漏点。\n"
        )
        # ``pytest_sessionfinish`` 抛异常或调用 ``pytest.exit`` 都会被 pytest 吞掉，
        # 进程仍然返回 0。这里直接用 ``os._exit`` 强制非零退出，触发 CI 报警。
        os._exit(1)
