"""SDK documentation lives beside the package and its major modules."""

from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_ROOT = REPO_ROOT / "sdk"
CORE_SOURCE = REPO_ROOT / "sdk" / "src" / "juice_agents" / "core"
MODULES = {
    "agent", "cron", "exporter", "graph", "group", "managers", "memory",
    "models", "permissions", "prebuilt", "registry", "runner", "team",
}
MARKDOWN_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def test_core_docs_live_beside_their_modules() -> None:
    # One README at each major module is the SDK documentation contract.
    assert (SDK_ROOT / "README.md").is_file()
    assert (CORE_SOURCE / "README.md").is_file()
    assert {path.name for path in CORE_SOURCE.iterdir() if path.is_dir() and path.name in MODULES} == MODULES
    for module_name in MODULES:
        assert (CORE_SOURCE / module_name / "README.md").is_file()
    assert not list(SDK_ROOT.rglob("docs"))
    assert not list(SDK_ROOT.rglob("DEVELOP.md"))
    assert not list(SDK_ROOT.rglob("CLAUDE.md"))
    assert (SDK_ROOT / "README.zh-CN.md").is_file()
    assert (CORE_SOURCE / "README.zh-CN.md").is_file()
    for module_name in MODULES:
        assert (CORE_SOURCE / module_name / "README.zh-CN.md").is_file()


def test_sdk_readme_links_resolve() -> None:
    # External URLs and in-page fragments are outside the local path contract.
    documents = [SDK_ROOT / "README.md", *CORE_SOURCE.rglob("README.md")]
    for document in documents:
        for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            parsed = urlsplit(raw_target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = document.parent / unquote(parsed.path)
            assert target.exists(), f"{document.relative_to(REPO_ROOT)} -> {raw_target}"
