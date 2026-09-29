"""Static Group declarations.

Runtime group coordination is supplied by the generic Runner managers.  The
legacy live-agent builder is intentionally not imported from this package.
"""

from .configs import build_default_group_manager_config, build_default_group_worker_configs

__all__ = ["build_default_group_manager_config", "build_default_group_worker_configs"]
