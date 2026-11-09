"""Configuration imports must not capture or require the process workspace."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def test_sdk_import_and_explicit_workspace_work_after_cwd_is_deleted(tmp_path: Path) -> None:
    """Reproduce getcwd() failure in a child so pytest keeps its valid cwd.

    The interpreter starts in an existing directory, then removes its cwd before
    the first SDK import. Absolute workspace paths must still allow reading
    configuration and credentials without guessing a replacement directory.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("JUICE_TEST_IMPORT_KEY=workspace-key\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    script = textwrap.dedent("""
        import os
        from pathlib import Path
        import sys

        root = Path(sys.argv[1])
        workspace = root / "workspace"
        removed = root / "removed"
        removed.mkdir()
        os.chdir(removed)
        removed.rmdir()

        try:
            import juice_agents
            from juice_agents.core.config import ConfigurationContext, DEFAULT_DOTENV_PATH
            from juice_agents.core.config.runtime_config import credential_paths
            import adapters.stdio_gateway.entry

            assert not DEFAULT_DOTENV_PATH.is_absolute()
            context = ConfigurationContext.from_workspace(workspace)
            assert context.dotenv_path == workspace / ".env"
            assert context.read_env_values()["JUICE_TEST_IMPORT_KEY"] == "workspace-key"
            assert context.get_mapping("providers")
            assert credential_paths(dotenv_path=workspace / ".env")[1] == context.dotenv_path
            # Importing and reading an explicit workspace must never chdir the
            # host process as a side effect, even when its old cwd is gone.
            try:
                os.getcwd()
            except FileNotFoundError:
                pass
            else:
                raise AssertionError("SDK unexpectedly changed the process cwd")
        finally:
            os.chdir(root)
    """)
    result = subprocess.run(
        [sys.executable, "-P", "-c", script, str(tmp_path)],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_default_credentials_follow_cwd_at_use_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing workspace after import must select the new project's .env."""
    from juice_agents.core.config import ConfigurationContext, DEFAULT_DOTENV_PATH
    from juice_agents.core.config import model_catalog, runtime_config

    # Keep the existing public exports, but resolve their relative path only
    # when it is actually used. No import-time workspace may be retained.
    assert DEFAULT_DOTENV_PATH == Path(".env")
    assert model_catalog.DEFAULT_DOTENV_PATH == runtime_config.DEFAULT_DOTENV_PATH
    for name in ("first", "second"):
        workspace = tmp_path / name
        workspace.mkdir()
        monkeypatch.chdir(workspace)
        assert DEFAULT_DOTENV_PATH.resolve() == workspace / ".env"
        assert runtime_config.credential_paths()[1] == workspace / ".env"
        assert ConfigurationContext.from_workspace(None).dotenv_path == workspace / ".env"
