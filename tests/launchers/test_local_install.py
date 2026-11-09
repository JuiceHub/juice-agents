"""Local launchers bind interpreters while preserving each project's environment."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _install(checkout: Path, directory: Path) -> Path:
    env = {**os.environ, "CONDA_DEFAULT_ENV": "juice-agents"}
    result = subprocess.run(
        ["/bin/bash", str(checkout / "install-juice"), "--bin-dir", str(directory)],
        env=env, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return directory / "juice"


def test_installed_command_runs_without_conda_and_handles_unicode_spaces(tmp_path: Path) -> None:
    """Exercise the installed executable, including quoting and stale path errors."""
    checkout = tmp_path / "源码 checkout 'quoted'"
    checkout.mkdir()
    for name in ("juice", "install-juice"):
        shutil.copy2(ROOT / name, checkout / name)
    # Reuse immutable source/dependencies without installing or changing locks.
    for name in ("sdk", "adapters", "frontend"):
        (checkout / name).symlink_to(ROOT / name, target_is_directory=True)
    launcher = _install(checkout, tmp_path / "用户 bin")
    body = launcher.read_text(encoding="utf-8")
    assert sys.executable in body
    assert str(Path(shutil.which("node")).resolve()) in body
    project = tmp_path / "我的 project"
    project.mkdir()
    env = {**os.environ, "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    for key in list(env):
        if key.startswith("CONDA") or key.startswith("JUICE_"):
            env.pop(key)
    result = subprocess.run([str(launcher), "--workspace", str(project), "--help"],
                            cwd=project, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--workspace" in result.stdout
    (checkout / "juice").unlink()
    broken = subprocess.run([str(launcher)], cwd=project, env=env, capture_output=True, text=True)
    assert broken.returncode != 0
    assert "source checkout is missing" in broken.stderr


def test_launcher_missing_dependencies_does_not_mutate_installation(tmp_path: Path) -> None:
    checkout = tmp_path / "missing dependencies"
    checkout.mkdir()
    shutil.copy2(ROOT / "juice", checkout / "juice")
    # Python fails before frontend lookup; a failed dependency check must only
    # explain the repair, never install packages or delete node_modules.
    package = checkout / "sdk" / "src" / "juice_agents"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("import juice_missing_test_dependency\n", encoding="utf-8")
    env = {**os.environ, "JUICE_PYTHON_PATH": sys.executable,
           "JUICE_NODE_PATH": shutil.which("node") or ""}
    result = subprocess.run([str(checkout / "juice")], cwd=tmp_path, env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "pip install -e" in result.stderr
    assert "juice_missing_test_dependency" in result.stderr
    assert sorted(path.name for path in checkout.iterdir()) == ["juice", "sdk"]
    shutil.rmtree(checkout / "sdk")
    for name in ("sdk", "adapters"):
        (checkout / name).symlink_to(ROOT / name, target_is_directory=True)
    frontend = checkout / "frontend"
    frontend.mkdir()
    shutil.copy2(ROOT / "frontend" / "check-cli-dependencies.mjs", frontend)
    sentinel = frontend / "node_modules" / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep", encoding="utf-8")
    env["JUICE_PYTHON_PATH"] = sys.executable
    missing_frontend = subprocess.run([str(checkout / "juice")], cwd=tmp_path, env=env,
                                     capture_output=True, text=True)
    assert missing_frontend.returncode != 0
    assert "npm ci" in missing_frontend.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (frontend / "package-lock.json").exists()


def test_launcher_reports_import_runtime_errors_without_dependency_advice(tmp_path: Path) -> None:
    """A filesystem failure during import must retain its traceback and cause."""
    checkout = tmp_path / "runtime failure"
    package = checkout / "sdk" / "src" / "juice_agents"
    package.mkdir(parents=True)
    shutil.copy2(ROOT / "juice", checkout / "juice")
    (package / "__init__.py").write_text(
        'raise FileNotFoundError("import-time filesystem failure")\n', encoding="utf-8"
    )
    env = {**os.environ, "JUICE_PYTHON_PATH": sys.executable,
           "JUICE_NODE_PATH": shutil.which("node") or ""}
    result = subprocess.run([str(checkout / "juice")], cwd=tmp_path, env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "FileNotFoundError: import-time filesystem failure" in result.stderr
    assert "Python startup failed" in result.stderr
    assert "dependencies are missing" not in result.stderr
    assert "pip install" not in result.stderr


def test_launcher_rejects_deleted_current_directory(tmp_path: Path) -> None:
    """Remove only the child's cwd, then execute the real source launcher."""
    deleted_workspace = tmp_path / "deleted project"
    deleted_workspace.mkdir()
    result = subprocess.run(
        ["/bin/bash", "-c", 'cd "$1" && rmdir "$1" && exec "$2"',
         "deleted-cwd-test", str(deleted_workspace), str(ROOT / "juice")],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "Current working directory is unavailable" in result.stderr
    assert "cd to an existing directory" in result.stderr
    assert "dependencies are missing" not in result.stderr
    assert "Traceback" not in result.stderr


def test_launcher_rejects_invalid_workspace_and_reserved_worktree(tmp_path: Path) -> None:
    launcher = _install(ROOT, tmp_path / "bin")
    for args, message in [(["--workspace", str(tmp_path / "absent")], "Workspace is missing"),
                          (["--workspace"], "requires a directory"),
                          (["--worktree", "branch"], "not implemented")]:
        result = subprocess.run([str(launcher), *args], cwd=tmp_path,
                                capture_output=True, text=True)
        assert result.returncode != 0
        assert message in result.stderr


def test_installed_cli_opens_project_catalog_and_exits_without_model_credentials(tmp_path: Path) -> None:
    """A real terminal drives the installed CLI and its actual Python gateway."""
    import fcntl
    import pty
    import select
    import struct
    import termios
    import time

    launcher = _install(ROOT, tmp_path / "bin")
    project = tmp_path / "真实 project"
    (project / ".juice").mkdir(parents=True)
    # The project's frontend compiler options must not recompile framework JSX.
    (project / "tsconfig.json").write_text(
        '{"compilerOptions":{"jsx":"react-jsx","jsxImportSource":"missing-project-only-jsx-runtime"}}',
        encoding="utf-8",
    )
    (project / ".juice" / "config.yaml").write_text(
        "providers:\n  offline:\n    backend: openai\n    api_base: http://127.0.0.1:1/v1\n"
        "    api_key_env: JUICE_UNUSED_TEST_KEY\nmodels:\n  cli_catalog_probe:\n"
        "    provider: offline\n    provider_model_name: offline\n", encoding="utf-8"
    )
    env = {**os.environ, "PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "TERM": "xterm-256color", "JUICE_NO_ANIMATION": "1", "NO_COLOR": "1",
           "TSX_DISABLE_CACHE": "1"}
    for key in list(env):
        if key.startswith("CONDA") or key.endswith("API_KEY"):
            env.pop(key)
    env.pop("JUICE_WORKSPACE_DIR", None)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 45, 140, 0, 0))
    child = subprocess.Popen([str(launcher)], cwd=project, env=env,
                             stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    transcript = bytearray()

    def await_text(expected: bytes, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    transcript.extend(os.read(master, 65536))
                except OSError:
                    break
            if expected in transcript:
                return
            if child.poll() is not None:
                break
        raise AssertionError(f"CLI did not show {expected!r}: {transcript.decode(errors='replace')[-12000:]}")

    try:
        await_text(b"lightweight terminal surface")
        os.write(master, b"/models")
        await_text(b"/models")
        os.write(master, b"\r")
        await_text(b"cli_catalog_probe")
        os.write(master, b"/exit")
        await_text(b"/exit")
        os.write(master, b"\r")
        child.wait(timeout=15)
        assert child.returncode == 0
        assert not (project / ".juice" / "sessions").exists(), "Catalog queries must remain cold"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        os.close(master)
