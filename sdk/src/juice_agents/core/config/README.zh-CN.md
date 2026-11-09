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
默认逻辑模型 `doubao_lite` 对应火山方舟模型 `doubao-seed-2-1-lite-260915`；
如有需要可覆盖其模型配置。

连接信息统一放在 `providers`，模型只引用 Provider。聊天 `/model` 与 GRM
`/rm` 独立选择同一个 `models` 目录中的模型；生图从 `image_models` 和
`image.model_name` 读取。`/image_model` 只切换模型，生图开关仍在 `/config`。

```yaml
providers:
  ark:
    backend: doubao
    api_base: https://ark.cn-beijing.volces.com/api/v3
    api_key_env: DOUBAO_API_KEY
    # api_base_env: ARK_BASE_URL
models:
  doubao_lite:
    provider: ark
    provider_model_name: doubao-seed-2-1-lite-260915
  doubao_pro:
    provider: ark
    provider_model_name: doubao-seed-2-0-pro-260215
image_models:
  seedream_4:
    provider: ark
    provider_model_name: doubao-seedream-4-0-250828
image:
  enabled: false
  model_name: seedream_4
```

聊天与 GRM 继续使用 `load_runtime_model_config`。生图使用
`load_runtime_image_model_config`：默认读取 `image.model_name`，也可显式指定
`model_name` 验证候选。两者读取最新 Provider 并遵循相同凭据优先级。
`list_runtime_models` / `list_runtime_image_models` 无需密钥，返回地址时隐藏
URL 中的凭据。生图首版支持 Ark 的 `doubao` backend，可配置多个 Provider 和部署点。

迁移旧配置时，把模型内的 `backend`、`api_base`、`api_base_env`、`api_key_env`
移入命名 Provider，模型里改为 `provider: <名称>`，保留 `provider_model_name`
及模型能力字段。旧内联字段或分层合并后混用新旧字段会给出迁移错误，不自动改写用户文件。

```python
from juice_agents.core.config import ConfigurationContext

context = ConfigurationContext.from_workspace("/path/to/project")
config = context.read_merged_config()  # 不要求凭据
model = context.resolve_model("doubao_lite")
```

开发约定：

- 不在导入时解析依赖工作区的路径。`DEFAULT_DOTENV_PATH` 保持相对路径 `Path(".env")`，由 `credential_paths` 或 `ConfigurationContext` 在使用时定位凭据。绝对工作区/凭据路径无需有效的进程 cwd，隐式路径则需要。
- Registry、模型及需密钥的工具传递同一个上下文。重新创建上下文时保留 `overrides` 和显式 `dotenv_path`。
- `project_config_path` 指定基础文件，不是项目覆盖文件。`workspace_config_path` 始终指向 `workspace/.juice/config.yaml`。
- 默认配置与本模块放在一起，由 `sdk/pyproject.toml` 声明打包 `defaults.yaml`。显式指定的基础文件不存在时直接报错，不回退到默认值。
- `read_workspace_config` / `write_workspace_config` 仅读写项目覆盖，不保存合并后的默认值或临时启动参数。
- 配置缓存逐来源检查文件指纹，返回相互隔离的值。凭据按需读取，不导出至 `os.environ`，不输出日志。显式为空的进程环境变量同样覆盖凭据文件。
- 复用模型客户端前重新解析当前配置与凭据；删除文件或修改凭据后不会继续命中过期实例。
- 连接字段只放在 `providers`，语言模型能力留在 `models`；不借用聊天模型作为生图连接，不新增 GRM 目录。GRM 选择保持进程内生命周期。

功能测试位于 `tests/core/config/` 和 `tests/core/registry/test_model_catalog.py`。
