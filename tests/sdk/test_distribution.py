"""The installable SDK must contain its public metadata and only SDK files."""

from __future__ import annotations

from email.parser import Parser
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from zipfile import ZipFile


SDK_ROOT = Path(__file__).resolve().parents[2] / "sdk"
PROJECT_URL = "https://github.com/JuiceHub/juice-agents"


def test_sdk_distribution_builds_and_imports_without_repository(tmp_path: Path) -> None:
    # Build a copy of the package: setuptools creates egg-info/build directories,
    # and no test should leave those artifacts in the source checkout.
    package_root = tmp_path / "sdk"
    package_root.mkdir()
    shutil.copy2(SDK_ROOT / "pyproject.toml", package_root)
    shutil.copy2(SDK_ROOT / "README.md", package_root)
    shutil.copy2(SDK_ROOT / "LICENSE", package_root)
    shutil.copytree(
        SDK_ROOT / "src" / "juice_agents",
        package_root / "src" / "juice_agents",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".juice"),
    )
    subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--sdist", "--wheel"],
        cwd=package_root,
        check=True,
        capture_output=True,
        text=True,
    )

    wheel = next((package_root / "dist").glob("*.whl"))
    source_archive = next((package_root / "dist").glob("*.tar.gz"))
    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_file = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_file).decode("utf-8"))
        license_file = next(name for name in names if name.endswith(".dist-info/licenses/LICENSE"))
        wheel_license = archive.read(license_file)

    assert metadata["Name"] == "juice-agents"
    assert metadata["License-Expression"] == "Apache-2.0"
    assert "LICENSE" in metadata.get_all("License-File")
    assert metadata["Requires-Python"] == ">=3.11"
    assert metadata["Description-Content-Type"] == "text/markdown"
    assert f"Homepage, {PROJECT_URL}" in metadata.get_all("Project-URL")
    assert "runner_config=\"agent\"" in metadata.get_payload()
    assert "juice_agents/py.typed" in names
    assert "juice_agents/_assets/config.example.yaml" in names
    assert "juice_agents/_assets/skills/joke-expert/SKILL.md" in names
    assert all(name.startswith("juice_agents/") or ".dist-info/" in name for name in names)
    assert not any("/.juice/" in name or name.endswith("/.env") for name in names)

    with tarfile.open(source_archive, "r:gz") as archive:
        source_names = archive.getnames()
        source_license = next(name for name in source_names if name.endswith("/LICENSE"))
        packaged_license = archive.extractfile(source_license)
        assert packaged_license is not None
        assert packaged_license.read() == wheel_license
    # A separate SDK license is needed for its standalone build; keep both
    # copies byte-for-byte identical to prevent conflicting public terms.
    assert wheel_license == (SDK_ROOT / "LICENSE").read_bytes()
    assert wheel_license == (SDK_ROOT.parent / "LICENSE").read_bytes()
    assert any(name.endswith("/README.md") and name.count("/") == 1 for name in source_names)
    assert any(name.endswith("/pyproject.toml") for name in source_names)
    assert not any("/adapters/" in name or "/backend/" in name for name in source_names)

    # A target install with no source checkout on PYTHONPATH catches editable-only
    # imports and package resources that were accidentally omitted from the wheel.
    target = tmp_path / "installed"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheel)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(target)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from importlib import resources; "
            "from pathlib import Path; "
            "import juice_agents; "
            "assert Path(juice_agents.__file__).resolve().is_relative_to(Path(__import__('sys').argv[1])); "
            "assert resources.files('juice_agents').joinpath('_assets', 'config.example.yaml').is_file()",
            str(target),
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
