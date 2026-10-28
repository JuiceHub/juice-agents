"""Load the instruction chain for a workspace without changing process cwd.

Only the Git root-to-start-directory chain is read. Descendant instructions
remain the Agent's responsibility when it later chooses files to edit.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import subprocess

logger = logging.getLogger(__name__)
MAX_RULE_BYTES = 32 * 1024


@dataclass(frozen=True, slots=True)
class RuleDocument:
    source: Path
    scope: Path
    text: str


@dataclass(frozen=True, slots=True)
class WorkspaceRules:
    documents: tuple[RuleDocument, ...]
    prompt: str
    diagnostics: tuple[str, ...]


def _project_chain(workspace: Path) -> list[Path]:
    """Git handles both ordinary repositories and linked-worktree .git files."""

    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        root = Path(result.stdout.strip()).resolve() if result.returncode == 0 else workspace
        relative = workspace.relative_to(root)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return [workspace]
    chain = [root]
    for component in relative.parts:
        chain.append(chain[-1] / component)
    return chain


def load_workspace_rules(workspace_dir: str | Path, *, max_bytes: int = MAX_RULE_BYTES) -> WorkspaceRules:
    """Read user then project rules, marking source/scope and bounding UTF-8 bytes.

    A whitespace-only override falls back to AGENTS.md in the same directory.
    The cap includes headings and source metadata, not just document bodies, so
    prompt size is predictable. Diagnostics name omitted/truncated sources.
    """

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    workspace = Path(workspace_dir).expanduser().resolve()
    if not workspace.is_dir():
        raise NotADirectoryError(f"workspace directory does not exist: {workspace}")
    diagnostics: list[str] = []
    documents: list[RuleDocument] = []
    user_root = Path.home().resolve() / ".juice"
    directories = [user_root, *_project_chain(workspace)]
    # A workspace can itself be ~/.juice. Never repeat a source in that case.
    for directory in dict.fromkeys(directories):
        for name in ("AGENTS.override.md", "AGENTS.md"):
            source = directory / name
            try:
                text = source.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError) as exc:
                diagnostic = f"Cannot read workspace rules {source}: {exc}"
                diagnostics.append(diagnostic)
                logger.warning(diagnostic)
                continue
            if text:
                documents.append(RuleDocument(source, directory, text))
                break

    if not documents:
        return WorkspaceRules((), "", tuple(diagnostics))
    prefix = (
        "# Workspace instructions\n"
        "Follow these rules within their stated directory scope. Later, deeper directory "
        "rules take precedence over earlier, broader rules. User-level rules apply to all "
        "workspaces. Before editing files in deeper directories, read their nonempty "
        "AGENTS.override.md or otherwise AGENTS.md, applying the same scope and precedence.\n"
    )
    blocks = [prefix]
    for doc in documents:
        scope = "all workspaces (user-level)" if doc.scope == user_root else f"{doc.scope} and descendants"
        blocks.append(f"\n## Source: {doc.source}\nScope: {scope}\n{doc.text}\n")
    raw = "".join(blocks).encode("utf-8")
    if len(raw) > max_bytes:
        offset = len(prefix.encode("utf-8"))
        affected = []
        for doc, block in zip(documents, blocks[1:]):
            offset += len(block.encode("utf-8"))
            if offset > max_bytes:
                affected.append(str(doc.source))
        diagnostic = f"Workspace rules truncated at {max_bytes} bytes; affected sources: {', '.join(affected)}"
        diagnostics.append(diagnostic)
        logger.warning(diagnostic)
    # Drop only an incomplete final UTF-8 code point; never inject replacement chars.
    prompt = raw[:max_bytes].decode("utf-8", errors="ignore")
    logger.info("workspace_rules_loaded workspace=%s sources=%d bytes=%d", workspace, len(documents), len(prompt.encode("utf-8")))
    return WorkspaceRules(tuple(documents), prompt, tuple(diagnostics))
