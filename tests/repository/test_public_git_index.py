"""The public Git index must contain only the reviewed source snapshot."""

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_tracked_paths_exclude_local_state_and_removed_layout() -> None:
    # Ignore rules alone do not protect a file that was tracked in an older
    # commit, so inspect the index that will actually be published.
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    tracked = [Path(raw.decode()) for raw in result.stdout.split(b"\0") if raw]
    forbidden = [
        str(path)
        for path in tracked
        if path.name in {".env", "config.ini", "CLAUDE.md", "DEVELOP.md"}
        or "docs" in path.parts
        or ".juice" in path.parts
        or path.parts[0] == "backend"
    ]
    assert forbidden == []
