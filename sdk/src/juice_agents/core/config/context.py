"""统一配置上下文管理。

ConfigurationContext 封装所有配置源和路径解析逻辑，替代到处传递
runtime_config_path / config_dir / tool_config_dir 的模式。

配置层级（高优先级 → 低优先级）：
  Runtime Override > Workspace > ~/.juice/config.yaml > SDK bundled defaults
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runtime_config import (
    DEFAULT_RUNTIME_CONFIG_PATH,
    read_credential_values,
    read_runtime_config,
    resolve_env_value,
    user_config_path,
    user_dotenv_path,
)


@dataclass(frozen=True)
class ConfigurationContext:
    """统一的配置上下文，管理所有配置源和路径解析。

    Attributes:
        project_config_path: SDK 默认配置或调用方显式传入的配置文件路径
        workspace_dir: workspace 根目录（可选）
        dotenv_path: .env 文件路径
    """

    project_config_path: Path
    workspace_dir: Path | None
    dotenv_path: Path
    overrides: dict[str, Any] = field(default_factory=dict)

    @property
    def global_config_path(self) -> Path:
        return user_config_path()

    @property
    def global_dotenv_path(self) -> Path:
        return user_dotenv_path()

    @property
    def workspace_config_path(self) -> Path | None:
        if self.workspace_dir is None:
            return None
        return self.workspace_dir / ".juice" / "config.yaml"

    @property
    def agents_dir(self) -> Path:
        if self.workspace_dir is not None:
            return self.workspace_dir / ".juice" / "agents"
        return Path.cwd().resolve() / ".juice" / "agents"

    @property
    def tools_dir(self) -> Path:
        if self.workspace_dir is not None:
            return self.workspace_dir / ".juice" / "tools"
        return Path.cwd().resolve() / ".juice" / "tools"

    @property
    def juice_root(self) -> Path:
        if self.workspace_dir is not None:
            return self.workspace_dir / ".juice"
        return Path.cwd().resolve() / ".juice"

    def read_merged_config(self) -> dict[str, Any]:
        """复用唯一的分层读取入口，保持 Runner、工具与前端口径一致。"""
        return read_runtime_config(
            self.project_config_path, workspace_dir=self.workspace_dir, overrides=self.overrides,
        )

    def read_env_values(self) -> dict[str, str]:
        """合并用户和项目凭据文件，不改写进程环境。"""
        return read_credential_values(workspace_dir=self.workspace_dir, dotenv_path=self.dotenv_path)

    def resolve_env(self, name: str) -> str:
        return resolve_env_value(name, dotenv_values=self.read_env_values())

    def get_mapping(self, mapping_name: str) -> dict[str, Any]:
        """从合并配置中提取指定顶层映射。"""
        payload = self.read_merged_config()
        raw_mapping = payload.get(mapping_name)
        if not isinstance(raw_mapping, dict):
            raise ValueError(f"运行时配置缺少合法的 {mapping_name} 顶层字段")
        return dict(raw_mapping)

    def resolve_model(
        self,
        model_name: str,
        *,
        model_effort: str | None = None,
    ) -> Any:
        """解析模型实例（封装 resolve_runtime_model 逻辑）。"""
        from .model_catalog import resolve_runtime_model

        return resolve_runtime_model(
            model_name=model_name,
            runtime_config_path=self.project_config_path,
            workspace_dir=self.workspace_dir,
            dotenv_path=self.dotenv_path,
            model_effort=model_effort,
            runtime_overrides=self.overrides,
        )

    @classmethod
    def from_workspace(
        cls,
        workspace_dir: str | Path | None,
        *,
        project_config_path: str | Path | None = None,
        dotenv_path: str | Path | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> "ConfigurationContext":
        """从 workspace 目录构建配置上下文。"""
        resolved_workspace = (
            Path(workspace_dir).expanduser().resolve() if workspace_dir else None
        )
        return cls(
            project_config_path=(
                Path(project_config_path).expanduser().resolve()
                if project_config_path is not None
                else DEFAULT_RUNTIME_CONFIG_PATH
            ),
            workspace_dir=resolved_workspace,
            dotenv_path=(
                Path(dotenv_path).expanduser().resolve()
                if dotenv_path is not None
                else (resolved_workspace or Path.cwd().resolve()) / ".env"
            ),
            overrides=deepcopy(overrides or {}),
        )


__all__ = [
    "ConfigurationContext",
]
