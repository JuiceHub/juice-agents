"""Repository documentation stays beside each major module."""

from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (ROOT / "sdk", ROOT / "adapters", ROOT / "examples", ROOT / "frontend", ROOT / "tests")
IGNORED_PARTS = {".git", ".tmp", ".cache", "__pycache__", "node_modules", ".pytest_cache"}
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


def test_project_readmes_have_english_defaults_and_chinese_translations() -> None:
    documents = sorted({ROOT / "README.md", *ROOT.glob("**/README.md")})
    documents = [
        path
        for path in documents
        if not any(part in IGNORED_PARTS for part in path.relative_to(ROOT).parts)
    ]
    assert len(documents) >= 20

    for document in documents:
        english = document.read_text(encoding="utf-8")
        chinese = document.with_name("README.zh-CN.md")
        assert chinese.is_file(), f"Missing Chinese translation for {document.relative_to(ROOT)}"
        english_body = "\n".join(
            line for line in english.splitlines()
            if line.strip() != "> [简体中文](README.zh-CN.md)"
        )
        assert not any("\u4e00" <= character <= "\u9fff" for character in english_body), (
            f"Default README must be English: {document.relative_to(ROOT)}"
        )
        assert "README.zh-CN.md" in english, (
            f"Missing Chinese language link in {document.relative_to(ROOT)}"
        )
        chinese_text = chinese.read_text(encoding="utf-8")
        assert chinese_text.startswith("> [English](README.md)"), (
            f"Missing English language link in {chinese.relative_to(ROOT)}"
        )
        assert sum("\u4e00" <= character <= "\u9fff" for character in chinese_text) >= 20, (
            f"Chinese translation is missing localized content: {chinese.relative_to(ROOT)}"
        )


def test_readme_links_resolve() -> None:
    documents = [*ROOT.glob("**/README.md"), *ROOT.glob("**/README.zh-CN.md")]
    documents = [
        path
        for path in documents
        if not any(part in IGNORED_PARTS for part in path.relative_to(ROOT).parts)
    ]
    assert len(documents) >= 40

    for document in documents:
        for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            parsed = urlsplit(raw_target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = document.parent / unquote(parsed.path)
            assert target.exists(), f"{document.relative_to(ROOT)} -> {raw_target}"
