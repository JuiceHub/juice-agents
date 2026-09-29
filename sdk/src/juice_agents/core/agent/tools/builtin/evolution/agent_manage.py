"""Agent 声明的校验与写入工具。

只读发现在 `builtin/agents/agents_tools.py`；本模块继承那里的
`AgentConfigToolBase`，因此读写共用同一套 Runner 身份解析、作用域投影与授权
判定，不存在第二份可被绕过的权限逻辑。

`validate` 无副作用；`save` 重跑完全相同的 schema、Tool/Skill/managed-agent/model
引用与 live candidate 校验，全部通过后才原子落盘 —— 校验失败不改变文件
bytes/mtime、关系或 pending refresh。
"""

from __future__ import annotations

import logging
from typing import Any

from juice_agents.core.registry.agents.registry import AgentRegistry
from juice_agents.core.registry.agents.types import (
    DEFAULT_MODEL_CONFIG_NAME,
    AgentConfig,
)
from juice_agents.core.registry.common import normalize_name
from juice_agents.core.registry.skills.registry import SkillRegistry

from ..agents.agents_tools import AgentConfigToolBase, AgentTarget

logger = logging.getLogger(__name__)


class AgentManageTool(AgentConfigToolBase):
    name = "agent_manage"
    # 与同一 step 内的其他动作串行，避免并发写 .juice/agents。
    description = (
        "校验或保存 Agent YAML，或仅卸载 managed-agent 引用；"
        "save 强制复用 validate 校验，能力在目标 Agent 下一 step 生效。"
    )
    inputs = {
        "action": {"type": "string", "description": "validate/save/unload"},
        "name": {"type": "string", "description": "Agent 名称"},
        "config": {"type": "object", "description": "validate/save 时的完整 Agent 配置", "required": False},
        "target_scope": {"type": "string", "description": "auto/self/authorized", "required": False},
    }
    outputs = {"result": {"type": "object", "description": "校验、原子保存或关系卸载结果"}}

    def _validate_references(self, candidate: AgentConfig, target: AgentTarget) -> None:
        identity = self._identity()
        if target.target_scope == "self" and not identity.is_root and candidate.managed_agent_names:
            raise PermissionError(f"{identity.role} 配置不能声明 managed_agent_names")
        if target.target_scope == "authorized" and candidate.managed_agent_names:
            # A manager/evolution worker editing a child must not grant that
            # child authority by writing managed-agent relationships into it.
            raise PermissionError(f"{target.role} 配置不能声明 managed_agent_names")

        registry = AgentRegistry(
            config_context=self._context(),
            config_dir=target.directory,
            tool_config_dir=self._context().tools_dir,
        )
        for ref in candidate.tools:
            registry._tool_registry.resolve(ref.name)  # noqa: SLF001 - shared registry validation boundary

        if candidate.skill_names:
            skills = SkillRegistry(
                local_dir=self._context().juice_root / "skills",
                config_path=self._context().project_config_path,
                workspace_dir=self._context().workspace_dir,
            )
            for skill_name in candidate.skill_names:
                skills.get(skill_name, include_disabled=True, ignore_global_enabled=True)

        if candidate.managed_agent_names:
            child_dir = target.directory
            for managed_name in candidate.managed_agent_names:
                managed_path = child_dir / f"{normalize_name(managed_name)}.yaml"
                builtin_target = AgentTarget(
                    name=managed_name,
                    mode_id=target.mode_id,
                    role="member",
                    target_scope="authorized",
                    directory=child_dir,
                )
                if not managed_path.is_file() and self._builtin_config(builtin_target) is None:
                    raise ValueError(f"未知 managed Agent 引用: {managed_name}")

        model_names = self._context().get_mapping("models")
        configured_model = str(candidate.model_config_name or DEFAULT_MODEL_CONFIG_NAME).strip()
        if configured_model in {"", DEFAULT_MODEL_CONFIG_NAME, "runtime.shared_model"}:
            configured_model = registry._workspace_runtime_defaults(  # noqa: SLF001
                self._context().project_config_path
            )["model_name"]
        if configured_model and configured_model not in model_names:
            raise ValueError(f"未知 model_config_name: {configured_model}")
        compression_model = candidate.session_compression.compact.compression_model_config_name
        if compression_model is not None and compression_model not in model_names:
            raise ValueError(f"未知 compression model 引用: {compression_model}")

        # Assemble the exact runtime candidate when a live model exists. This
        # catches invalid Tool params and Agent protocol construction before
        # save; the candidate is discarded and has no relation/refresh effects.
        live_model = getattr(self.owner_agent, "model", None)
        if live_model is not None:
            registry.instantiate(
                candidate,
                model=live_model,
                runtime_config_path=self._context().project_config_path,
                juice_root=self._context().juice_root,
            )

    def _validate(self, target: AgentTarget, config: dict[str, Any] | None) -> AgentConfig:
        if config is None:
            try:
                payload = self._view_target(target)["config"]
            except FileNotFoundError as exc:
                raise ValueError("validate/save 需要完整 Agent config") from exc
        else:
            payload = dict(config)
        # The route owns identity. An editable payload can neither redirect the
        # path nor impersonate a more privileged Agent by changing its name.
        payload["name"] = target.name
        candidate = AgentConfig.from_dict(payload, config_name=target.name)
        self._validate_references(candidate, target)
        return candidate

    def _relationship_owner(self, target: AgentTarget) -> Any | None:
        if target.target_scope == "self":
            return None
        runner = self._runner()
        identity = self._identity()
        if runner is None or identity.role != "evolution_worker":
            return self.owner_agent
        return runner.agent_manager.get(runner.root_agent_id).instance

    def _live_target(self, target: AgentTarget) -> Any | None:
        if target.target_scope == "self":
            return self.owner_agent
        runner = self._runner()
        if runner is None:
            return None
        for managed in runner.agent_manager.live_agents.values():
            agent = managed.instance
            if agent is self.owner_agent or managed.is_root:
                continue
            if str(getattr(agent, "name", "") or "").strip() == target.name:
                return agent
        return None

    def forward(
        self,
        action: str,
        name: str,
        config: dict[str, Any] | None = None,
        target_scope: str = "auto",
    ) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        target = self._resolve_target(
            name,
            target_scope,
            allow_new_relationship=normalized_action in {"validate", "save"},
        )

        if normalized_action in {"validate", "save"}:
            candidate = self._validate(target, config)
            if normalized_action == "validate":
                return {
                    "result": {
                        "action": "validate",
                        "name": candidate.name,
                        "valid": True,
                        "path": str(target.path),
                        **self._target_metadata(target),
                        "config": candidate.to_dict(),
                    }
                }

            # Validation has completed without touching the registry,
            # relationships or pending refresh. The static store performs an
            # atomic replace only now; no collaboration mode owns a branch.
            saved = AgentRegistry(
                config_context=self._context(),
                config_dir=target.directory,
            ).save_config(candidate, name=target.name)
            from juice_agents.core.agent.runtime_reconciler import activate_saved_agent

            runtime_refresh = activate_saved_agent(
                self.owner_agent,
                saved,
                config_dir=target.directory,
                target_agent=self._live_target(target),
                relationship_owner=self._relationship_owner(target),
                target_is_self=target.target_scope == "self",
            )
            logger.info(
                "agent_manage 保存并登记刷新: requester=%s target=%s scope=%s path=%s status=%s",
                self._identity().agent_name,
                saved.name,
                target.target_scope,
                target.path,
                runtime_refresh["status"],
            )
            return {
                "result": {
                    "action": "save",
                    "name": saved.name,
                    "path": str(target.path),
                    **self._target_metadata(target),
                    "config": saved.to_dict(),
                    "runtime_refresh": runtime_refresh,
                }
            }

        if normalized_action == "unload":
            from juice_agents.core.agent.runtime_reconciler import unload_agent

            runtime_refresh = unload_agent(
                self.owner_agent,
                target.name,
                target_is_self=target.target_scope == "self",
                relationship_owner=self._relationship_owner(target),
                target_agent=self._live_target(target),
            )
            logger.info(
                "agent_manage 仅卸载引用: requester=%s target=%s scope=%s path=%s status=%s",
                self._identity().agent_name,
                target.name,
                target.target_scope,
                target.path,
                runtime_refresh["status"],
            )
            return {
                "result": {
                    "action": "unload",
                    "name": target.name,
                    "path": str(target.path) if target.path.is_file() else None,
                    **self._target_metadata(target),
                    "runtime_refresh": runtime_refresh,
                }
            }
        raise ValueError("agent_manage action 必须为 validate/save/unload")


__all__ = ["AgentManageTool"]
