"""Repository documentation stays beside each major module."""

from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (ROOT / "sdk", ROOT / "adapters", ROOT / "examples", ROOT / "frontend", ROOT / "tests")
IGNORED_PARTS = {".tmp", ".cache", "__pycache__", "node_modules", ".pytest_cache"}
MARKDOWN_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def _source_files() -> list[Path]:
    return [
        path
        for source_root in SOURCE_ROOTS
        for path in source_root.rglob("*")
        if not any(part in IGNORED_PARTS for part in path.relative_to(ROOT).parts)
    ]


def test_documentation_uses_module_readmes() -> None:
    assert (ROOT / "README.md").is_file()
    assert (ROOT / "AGENTS.md").is_file()
    for module in ("sdk", "adapters", "examples", "frontend/cli", "frontend/shared", "frontend/web", "tests/core"):
        assert (ROOT / module / "README.md").is_file(), module

    paths = _source_files()
    assert not [path for path in paths if path.is_dir() and path.name == "docs"]
    assert not [path for path in paths if path.is_file() and path.name in {"DEVELOP.md", "CLAUDE.md"}]
    assert not (ROOT / "DEVELOP.md").exists()
    assert not (ROOT / "CLAUDE.md").exists()


def test_root_readme_links_resolve() -> None:
    document = ROOT / "README.md"
    for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
        parsed = urlsplit(raw_target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        target = document.parent / unquote(parsed.path)
        assert target.exists(), raw_target
