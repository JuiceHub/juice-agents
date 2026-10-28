> [English](README.md)

# 配置

所有消费者复用 `ConfigurationContext` 和 `read_runtime_config`：

```text
SDK 默认 / 显式 runtime_config_path
  → ~/.juice/config.yaml
  → workspace/.juice/config.yaml
  → 本次启动覆盖

凭据：进程环境变量 > workspace/.env > ~/.juice/.env
```

`defaults.yaml` 是随 wheel 和 sdist 发布的 SDK 只读基础配置。用户级或项目级文件只需
覆盖必要字段，无需复制模板。测试验证安装后的 SDK 可脱离仓库加载配置，且保存项目
覆盖不会修改包内默认值。

```python
from juice_agents.core.config import ConfigurationContext

context = ConfigurationContext.from_workspace("/path/to/project")
config = context.read_merged_config()  # 不要求凭据
model = context.resolve_model("doubao_lite")
```

开发约定：

- Registry、模型及需密钥的工具传递同一个上下文。重新创建上下文时保留 `overrides` 和显式 `dotenv_path`。
- `project_config_path` 指定基础文件，不是项目覆盖文件。`workspace_config_path` 始终指向 `workspace/.juice/config.yaml`。
- 默认配置与本模块放在一起，由 `sdk/pyproject.toml` 声明打包 `defaults.yaml`。显式指定的基础文件不存在时直接报错，不回退到默认值。
- `read_workspace_config` / `write_workspace_config` 仅读写项目覆盖，不保存合并后的默认值或临时启动参数。
- 配置缓存逐来源检查文件指纹，返回相互隔离的值。凭据按需读取，不导出至 `os.environ`，不输出日志。显式为空的进程环境变量同样覆盖凭据文件。
- 复用模型客户端前重新解析当前配置与凭据；删除文件或修改凭据后不会继续命中过期实例。

功能测试位于 `tests/core/config/` 和 `tests/core/registry/test_model_catalog.py`。
