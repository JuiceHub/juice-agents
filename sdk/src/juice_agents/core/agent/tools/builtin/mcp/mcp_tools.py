"""
MCP (Model Context Protocol) 工具接入。

目标：
- 支持通过 MCP server 配置连接服务器（当前实现 stdio transport）
- 通过 get_tools() 拉取 server 侧工具，并包装为本项目 Tool 子类供 Agent 使用

依赖说明：
- 本模块对 `mcp` Python SDK 为可选依赖；未安装时导入不会失败，
  但在尝试连接服务器时会抛出带安装提示的 ImportError。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...runtime.base_tools import CONTENT_OBSERVATION_CHARS, Tool
from juice_agents.core.runner.execution.cancellation import StreamCancelled, raise_if_cancelled

logger = logging.getLogger(__name__)


def _optional_import_mcp() -> tuple[bool, Any, Any, Any]:
    """
    返回 (available, ClientSession, StdioServerParameters, stdio_client)
    """
    try:
        from mcp import ClientSession, StdioServerParameters  # type: ignore
        from mcp.client.stdio import stdio_client  # type: ignore

        return True, ClientSession, StdioServerParameters, stdio_client
    except Exception:  # pragma: no cover - 运行时依赖缺失分支
        return False, None, None, None


def _as_dict(obj: Any) -> Dict[str, Any]:
    """尽量将 SDK 对象转换为 dict，失败则返回 {}。"""
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()  # type: ignore[misc]
            except Exception:
                pass
    return {}


def _get_field(obj: Any, *names: str, default: Any = None) -> Any:
    """从对象或 dict 中获取字段（兼容不同命名风格）。"""
    if isinstance(obj, dict):
        for n in names:
            if n in obj:
                return obj.get(n)
        return default
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _json_schema_to_inputs(input_schema: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    将 MCP tool 的 JSON Schema（通常是 object schema）转换为本项目 Tool.inputs 格式。
    """
    schema_type = input_schema.get("type")
    if schema_type not in (None, "object"):
        # 兜底：不是 object schema 时不展开参数
        return {}

    props: Dict[str, Any] = input_schema.get("properties") or {}
    required_list = set(input_schema.get("required") or [])

    inputs: Dict[str, Dict[str, Any]] = {}
    for name, meta in props.items():
        if not isinstance(meta, dict):
            meta = {}
        t = meta.get("type")
        if isinstance(t, list):
            # 当返回类型是列表时，拼接为可读的联合类型描述
            t = "|".join(str(x) for x in t)
        inputs[name] = {
            "type": str(t) if t else "any",
            "description": str(meta.get("description") or ""),
            "required": name in required_list,
        }
    return inputs


@dataclass(frozen=True)
class MCPServerConfig:
    """
    MCP server 配置（当前仅支持 stdio）。
    """

    name: str
    transport: str = "stdio"
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Optional[Dict[str, str]] = None

    # 工具命名空间：避免不同 server 的同名 tool 冲突
    namespace_tools: bool = True
    # 命名空间分隔符（默认 "."；CodeActAgent 场景建议用 "__" 以满足 Python 标识符）
    namespace_separator: str = "."

    # 调用超时（秒）
    timeout_s: float = 30.0

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("MCPServerConfig.name 必须为非空字符串")
        if self.transport != "stdio":
            raise ValueError(f"当前仅支持 transport=stdio，收到: {self.transport}")
        if not isinstance(self.command, str) or not self.command.strip():
            raise ValueError("MCPServerConfig.command 必须为非空字符串（stdio 启动命令）")
        if not isinstance(self.args, list) or any(not isinstance(x, str) for x in self.args):
            raise ValueError("MCPServerConfig.args 必须为 List[str]")
        if self.env is not None:
            if not isinstance(self.env, dict) or any(
                (not isinstance(k, str) or not isinstance(v, str)) for k, v in self.env.items()
            ):
                raise ValueError("MCPServerConfig.env 必须为 Dict[str, str] 或 None")
        if not isinstance(self.namespace_tools, bool):
            raise ValueError("MCPServerConfig.namespace_tools 必须为 bool")
        if not isinstance(self.namespace_separator, str) or not self.namespace_separator:
            raise ValueError("MCPServerConfig.namespace_separator 必须为非空字符串")
        if not isinstance(self.timeout_s, (int, float)) or self.timeout_s <= 0:
            raise ValueError("MCPServerConfig.timeout_s 必须为正数")


class MCPServerConnection:
    """
    一个 MCP server 的持久连接（stdio + 后台事件循环线程）。

    说明：
    - Tool.forward 是同步接口，但 MCP SDK 是异步接口；因此这里用后台线程运行事件循环，
      并通过 run_coroutine_threadsafe 将 list_tools / call_tool 变为同步等待。
    """

    def __init__(self, config: MCPServerConfig):
        config.validate()
        self.config = config

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ready = threading.Event()

        self._start_error: Optional[BaseException] = None
        self._session: Any = None
        self._stop_event: Any = None  # asyncio.Event, created in loop thread

        self._mcp_available, self._ClientSession, self._StdioServerParameters, self._stdio_client = _optional_import_mcp()

    def start(self) -> None:
        if not self._mcp_available:
            raise ImportError('未安装 mcp 依赖，无法连接 MCP server。请先执行: pip install "mcp"')

        with self._lock:
            if self._thread and self._thread.is_alive():
                return

            self._ready.clear()
            self._start_error = None

            self._thread = threading.Thread(
                target=self._thread_main,
                name=f"mcp-conn-{self.config.name}",
                daemon=True,
            )
            self._thread.start()

        if not self._ready.wait(timeout=self.config.timeout_s):
            raise TimeoutError(f"MCP server {self.config.name} 连接初始化超时（>{self.config.timeout_s}s）")
        if self._start_error is not None:
            raise RuntimeError(f"MCP server {self.config.name} 初始化失败: {self._start_error}") from self._start_error

    def close(self) -> None:
        with self._lock:
            loop = self._loop
            stop_event = self._stop_event
            thread = self._thread

        if loop and stop_event:
            try:
                loop.call_soon_threadsafe(stop_event.set)  # type: ignore[attr-defined]
            except Exception:
                pass

        if thread and thread.is_alive():
            thread.join(timeout=min(5.0, self.config.timeout_s))

    def list_tools(self) -> List[Dict[str, Any]]:
        self.start()
        loop = self._require_loop()
        fut = asyncio.run_coroutine_threadsafe(self._list_tools_async(), loop)
        res = fut.result(timeout=self.config.timeout_s)
        return res

    def call_tool(self, tool_name: str, arguments: Dict[str, Any], cancel_event: threading.Event | None = None) -> Any:
        self.start()
        loop = self._require_loop()
        fut = asyncio.run_coroutine_threadsafe(self._call_tool_async(tool_name, arguments), loop)
        deadline = time.monotonic() + float(self.config.timeout_s)
        while True:
            try:
                raise_if_cancelled(cancel_event)
            except StreamCancelled:
                fut.cancel()
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fut.cancel()
                raise TimeoutError(f"MCP tool {tool_name} 调用超时（>{self.config.timeout_s}s）")
            try:
                return fut.result(timeout=min(0.1, remaining))
            except concurrent.futures.TimeoutError:
                continue
            except StreamCancelled:
                fut.cancel()
                raise

    def _require_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("MCP 事件循环未初始化")
        return self._loop

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._async_main())
        except BaseException as exc:  # pragma: no cover - 线程启动失败保护
            self._start_error = exc
            self._ready.set()
            logger.exception("MCPServerConnection 后台线程异常退出: %s", exc)

    async def _async_main(self) -> None:
        assert self._mcp_available
        ClientSession = self._ClientSession
        StdioServerParameters = self._StdioServerParameters
        stdio_client = self._stdio_client

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()

        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=self.config.env,
        )

        logger.info(
            "连接 MCP server (stdio): name=%s command=%s args=%s",
            self.config.name,
            self.config.command,
            self.config.args,
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    await self._stop_event.wait()
        except BaseException as exc:
            self._start_error = exc
            self._ready.set()
            raise
        finally:
            logger.info("MCP server 连接已关闭: %s", self.config.name)

    async def _list_tools_async(self) -> List[Dict[str, Any]]:
        if self._session is None:
            raise RuntimeError("MCP session 未就绪")

        result = await self._session.list_tools()
        tools_obj = _get_field(result, "tools", default=result)
        tools_list: Iterable[Any] = tools_obj or []

        out: List[Dict[str, Any]] = []
        for t in tools_list:
            t_dict = _as_dict(t)
            name = _get_field(t, "name", default=t_dict.get("name", ""))
            desc = _get_field(t, "description", default=t_dict.get("description", ""))
            schema = _get_field(t, "inputSchema", "input_schema", default=t_dict.get("inputSchema") or t_dict.get("input_schema") or {})
            out.append({"name": name, "description": desc, "input_schema": schema})
        return out

    async def _call_tool_async(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if self._session is None:
            raise RuntimeError("MCP session 未就绪")

        # 不同 SDK 版本参数名可能是 (name, arguments=...) 或 (name, {...})
        try:
            return await self._session.call_tool(tool_name, arguments=arguments)
        except TypeError:
            return await self._session.call_tool(tool_name, arguments)


class MCPRemoteTool(Tool):
    """
    将 MCP server 侧的 tool 包装为本项目 Tool 子类。
    """

    max_observation_chars = CONTENT_OBSERVATION_CHARS

    def __init__(
        self,
        *,
        server_name: str,
        remote_name: str,
        description: str,
        input_schema: Dict[str, Any],
        connection: MCPServerConnection,
        exposed_name: str,
    ) -> None:
        super().__init__()
        self.server_name = server_name
        self.remote_name = remote_name
        self.name = exposed_name
        self.description = description or f"MCP tool {remote_name}"
        self.inputs = _json_schema_to_inputs(input_schema or {})
        self.outputs = {"result": {"type": "any", "description": "MCP 工具调用结果（原样返回）"}}
        self._connection = connection
        self.owner_agent: Any | None = None

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def forward(self, **kwargs: Any) -> Any:  # noqa: ANN401 - 工具允许任意入参
        logger.info(
            "调用 MCP 工具: %s (server=%s remote=%s) args=%s",
            self.name,
            self.server_name,
            self.remote_name,
            list(kwargs.keys()),
        )
        context = getattr(self.owner_agent, "runner_context", None) if self.owner_agent is not None else None
        cancel_event = getattr(context, "cancel_event", None)
        try:
            return self._connection.call_tool(self.remote_name, dict(kwargs), cancel_event=cancel_event)
        except TypeError:
            return self._connection.call_tool(self.remote_name, dict(kwargs))


class MCPTools:
    """
    MCP 工具管理器：基于配置连接一个或多个 server，并将其工具包装为 Tool。

    用法示例：
        mcp_tools = MCPTools([{"name": "calc", "command": "python", "args": ["examples/mcp_demo_server.py"]}])
        tools = mcp_tools.get_tools()
    """

    def __init__(self, servers: List[Dict[str, Any]] | List[MCPServerConfig]):
        if not isinstance(servers, list) or not servers:
            raise ValueError("servers 必须为非空 list")

        self._servers: List[MCPServerConfig] = []
        for s in servers:
            if isinstance(s, MCPServerConfig):
                cfg = s
            elif isinstance(s, dict):
                cfg = MCPServerConfig(
                    name=str(s.get("name", "")).strip(),
                    transport=str(s.get("transport", "stdio")).strip() or "stdio",
                    command=str(s.get("command", "")).strip(),
                    args=list(s.get("args") or []),
                    env=s.get("env"),
                    namespace_tools=bool(s.get("namespace_tools", True)),
                    namespace_separator=str(s.get("namespace_separator", ".") or "."),
                    timeout_s=float(s.get("timeout_s", 30.0)),
                )
            else:
                raise TypeError("servers 元素必须为 dict 或 MCPServerConfig")
            cfg.validate()
            self._servers.append(cfg)

        self._connections: Dict[str, MCPServerConnection] = {
            cfg.name: MCPServerConnection(cfg) for cfg in self._servers
        }
        self._tools_cache: Optional[List[Tool]] = None

    def close(self) -> None:
        for conn in self._connections.values():
            try:
                conn.close()
            except Exception:  # pragma: no cover - close 保护
                logger.exception("关闭 MCP 连接失败")

    def __enter__(self) -> "MCPTools":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()

    def get_tools(self, refresh: bool = False) -> List[Tool]:
        """
        获取所有 server 的 tools，并包装为 Tool 子类。

        Args:
            refresh: 是否强制重新从 server 拉取工具列表
        """
        if self._tools_cache is not None and not refresh:
            return list(self._tools_cache)

        tools: List[Tool] = []
        for cfg in self._servers:
            conn = self._connections[cfg.name]
            remote_tools = conn.list_tools()
            logger.info("MCP server=%s 拉取到 %d 个 tools", cfg.name, len(remote_tools))

            for t in remote_tools:
                remote_name = str(t.get("name", "")).strip()
                if not remote_name:
                    continue
                exposed_name = (
                    f"{cfg.name}{cfg.namespace_separator}{remote_name}"
                    if cfg.namespace_tools
                    else remote_name
                )
                tools.append(
                    MCPRemoteTool(
                        server_name=cfg.name,
                        remote_name=remote_name,
                        description=str(t.get("description") or ""),
                        input_schema=t.get("input_schema") or {},
                        connection=conn,
                        exposed_name=exposed_name,
                    )
                )

        self._tools_cache = list(tools)
        return tools


__all__ = ["MCPServerConfig", "MCPServerConnection", "MCPRemoteTool", "MCPTools"]
