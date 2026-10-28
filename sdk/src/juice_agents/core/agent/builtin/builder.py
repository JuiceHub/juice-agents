"""Build the Registry-resolved root without materializing built-in declarations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from juice_agents.core.registry import AgentRegistry
from juice_agents.core.registry.agents.types import AgentConfig, normalize_agent_type
from juice_agents.core.registry.tools.types import ToolRef
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.config.model_catalog import DEFAULT_RUNTIME_CONFIG_PATH
from juice_agents.core.config.runtime_config import read_runtime_config
from juice_agents.core.agent.tools.builtin.memory.memory_tools import (
    MemoryForgetTool,
    MemoryReadTool,
    MemorySearchTool,
    MemoryStatusTool,
    MemoryWriteTool,
)
from juice_agents.core.memory.config import get_memory_config
from juice_agents.core.memory.prompt import build_memory_prompt_context
from juice_agents.core.runner.workspace import RuntimeWorkspace, bind_workspace_to_agent_config


def _resolve_path(value: str | Path | None, default: Path) -> Path:
    return Path(value).expanduser().resolve() if value is not None else default


def _runtime_workspace(workspace_dir: str | Path | None) -> RuntimeWorkspace:
    if workspace_dir is None:
        return RuntimeWorkspace(workspace_dir=None)
    resolved = Path(workspace_dir).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return RuntimeWorkspace(workspace_dir=resolved)


def _prepare_config(
    config: AgentConfig,
    *,
    runtime_workspace: RuntimeWorkspace,
    default_plan_base_dir: str | Path,
) -> AgentConfig:
    return AgentConfig.from_dict(bind_workspace_to_agent_config(
        config.to_dict(),
        runtime_workspace=runtime_workspace,
        default_plan_base_dir=default_plan_base_dir,
    ))


def _refresh_prompt(agent: Any) -> None:
    """
    managed_agent_names 是 prompt 上下文的一部分；安装内置子智能体后必须重渲染。
    """
    if hasattr(agent, "refresh_executor_tools"):
        agent.refresh_executor_tools()
    refresh = getattr(agent, "refresh_system_prompt", None)
    if callable(refresh):
        refresh()
        return
    if hasattr(agent, "init_system_prompt"):
        agent.system_prompt = agent.init_system_prompt()
        if getattr(agent, "session", None) is not None:
            agent.session.system_prompt = agent.system_prompt


def _browser_tools_enabled(
    *,
    runtime_config_path: str | Path | None,
    runtime_workspace: RuntimeWorkspace,
) -> bool:
    """Return the merged browser.enabled flag; missing config keeps defaults on."""

    try:
        payload = read_runtime_config(
            runtime_config_path,
            workspace_dir=runtime_workspace.workspace_dir,
        )
    except Exception:
        return True
    raw_browser = payload.get("browser") if isinstance(payload, dict) else {}
    if not isinstance(raw_browser, dict):
        return True
    return bool(raw_browser.get("enabled", True))


def _image_generation_enabled(
    *,
    runtime_config_path: str | Path | None,
    runtime_workspace: RuntimeWorkspace,
) -> bool:
    """Return the merged image.enabled flag; image generation defaults off."""

    try:
        payload = read_runtime_config(
            runtime_config_path,
            workspace_dir=runtime_workspace.workspace_dir,
        )
    except Exception:
        return False
    raw_image = payload.get("image") if isinstance(payload, dict) else {}
    if not isinstance(raw_image, dict):
        return False
    return bool(raw_image.get("enabled", False))


def _without_browser_tools(config: AgentConfig, *, browser_enabled: bool) -> AgentConfig:
    """Remove Playwright browser tool refs from an agent config when disabled."""

    if browser_enabled:
        return config
    return config.copy_with(
        tools=[tool_ref.to_dict() for tool_ref in config.tools if not tool_ref.name.startswith("browser_")]
    )


def _with_image_generation_tool(config: AgentConfig, *, image_enabled: bool) -> AgentConfig:
    """Expose generate/edit image only when image.enabled is explicitly on."""

    tools = [tool_ref for tool_ref in config.tools if tool_ref.name != "generate_edit_image"]
    if image_enabled:
        tools.append(ToolRef(name="generate_edit_image"))
    return config.copy_with(tools=[tool_ref.to_dict() for tool_ref in tools])


def _install_memory_capabilities(
    agent: Any,
    *,
    workspace_dir: str | Path | None,
    runtime_config_path: str | Path | None,
) -> None:
    """Attach memory tools and prompt context to the root prebuilt agent."""

    if workspace_dir is None:
        return
    config = get_memory_config(
        runtime_config_path=runtime_config_path,
        workspace_dir=workspace_dir,
    )
    if not config.enabled:
        agent.memory_context = {"memory_enabled": False, "memory_dir": "", "memory_index": ""}
        _refresh_prompt(agent)
        return
    for tool in [
        MemoryReadTool(workspace_dir=workspace_dir, enabled=config.enabled, dream_enabled=config.dream.enabled),
        MemorySearchTool(workspace_dir=workspace_dir, enabled=config.enabled, dream_enabled=config.dream.enabled),
        MemoryWriteTool(workspace_dir=workspace_dir, enabled=config.enabled, dream_enabled=config.dream.enabled),
        MemoryForgetTool(workspace_dir=workspace_dir, enabled=config.enabled, dream_enabled=config.dream.enabled),
        MemoryStatusTool(workspace_dir=workspace_dir, enabled=config.enabled, dream_enabled=config.dream.enabled),
    ]:
        agent.tools[tool.name] = tool
    agent.memory_context = build_memory_prompt_context(workspace_dir=workspace_dir, config=config)
    _refresh_prompt(agent)


def _build_create_kwargs(
    *,
    runtime_config_path: str | Path | None,
    model_name: str | None,
    model_effort: str | None,
    juice_root: str | Path | None,
) -> dict[str, Any]:
    create_kwargs = {
        "runtime_config_path": _resolve_path(runtime_config_path, DEFAULT_RUNTIME_CONFIG_PATH),
    }
    if str(model_name or "").strip():
        create_kwargs["model_name"] = str(model_name or "").strip()
    if model_effort is not None:
        create_kwargs["model_effort"] = str(model_effort)
    if juice_root is not None:
        create_kwargs["juice_root"] = Path(juice_root).expanduser().resolve()
    return create_kwargs


def build_general_agent(
    *,
    runtime_config_path: str | Path | None = DEFAULT_RUNTIME_CONFIG_PATH,
    model_name: str | None = None,
    model_effort: str | None = None,
    workspace_dir: str | Path | None = None,
    agent_type: str | None = None,
):
    """Build ``root`` from the effective Registry declaration.

    Local ``root.yaml`` overrides the built-in root. Workspace paths, feature
    switches and an explicit protocol apply only to the runtime copy, so
    construction never writes portable root or child declarations.
    """
    runtime_workspace = _runtime_workspace(workspace_dir)
    default_plan_base_dir = runtime_workspace.writable_base_dir()
    juice_root = (
        runtime_workspace.juice_root(default_base_dir=default_plan_base_dir)
        if runtime_workspace.enabled
        else None
    )
    factory = AgentRegistry(
        config_context=ConfigurationContext.from_workspace(
            runtime_workspace.workspace_dir,
            project_config_path=runtime_config_path,
        ),
    )
    declared_config = factory.load_config("root")
    create_kwargs = _build_create_kwargs(
        runtime_config_path=runtime_config_path,
        model_name=model_name,
        model_effort=model_effort,
        juice_root=juice_root,
    )
    browser_enabled = _browser_tools_enabled(
        runtime_config_path=runtime_config_path,
        runtime_workspace=runtime_workspace,
    )
    image_enabled = _image_generation_enabled(
        runtime_config_path=runtime_config_path,
        runtime_workspace=runtime_workspace,
    )
    runtime_config = _prepare_config(
        _with_image_generation_tool(
            _without_browser_tools(declared_config, browser_enabled=browser_enabled),
            image_enabled=image_enabled,
        ),
        runtime_workspace=runtime_workspace,
        default_plan_base_dir=default_plan_base_dir,
    )
    if agent_type is not None:
        runtime_config = runtime_config.copy_with(
            agent_type=normalize_agent_type(agent_type)
        )
    root_agent = factory.instantiate(runtime_config, **create_kwargs)
    # Registry metadata must retain the portable declaration. Later management
    # saves start from this value and must not persist transient bound paths or
    # feature/protocol overrides from the runtime configuration above.
    root_agent._declared_agent_config = AgentConfig.from_dict(declared_config)
    root_agent._declared_tool_refs = tuple(declared_config.tools)
    _install_memory_capabilities(
        root_agent,
        workspace_dir=runtime_workspace.workspace_dir,
        runtime_config_path=runtime_config_path,
    )
    return root_agent


__all__ = ["build_general_agent"]
