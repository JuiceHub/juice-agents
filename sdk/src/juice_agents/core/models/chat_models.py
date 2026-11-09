"""聊天模型：统一 generate 接口及各 provider 的请求、重试与响应解析。"""

import asyncio
import inspect
import logging
import math
from typing import Any, Dict, List, Optional

from juice_agents.core.agent.agent_type import image_to_api_source
from juice_agents.core.agent.context.content import CACHE_BOUNDARY_KEY

from ..utils import normalize_token_usage


logger = logging.getLogger(__name__)


def _get_field(obj: Any, field: str) -> Optional[Any]:
    """从对象或字典中安全读取字段。"""
    if hasattr(obj, field):
        return getattr(obj, field)
    if isinstance(obj, dict):
        return obj.get(field)
    return None


class _EmptyModelResponseError(RuntimeError):
    """
    模型返回空内容时的内部异常。

    模型层仅对空响应重试；网络失败由官方 SDK 重试。
    最终如何提示给 agent 由上层协议层决定。
    """


class _AsyncModelLifecycle:
    """聊天和评分共用的异步连接生命周期，不创建线程或事件循环。

    一个客户端只属于首次调用它的事件循环；并发任务可共享该连接池，
    跨事件循环复用则明确报错。注入的客户端始终由调用方关闭。
    """

    _owns_client: bool = False
    _client: Any = None
    _owner_loop: asyncio.AbstractEventLoop | None = None
    _closed = False

    def _check_loop(self) -> None:
        if self._closed:
            raise RuntimeError("模型已经关闭")
        loop = asyncio.get_running_loop()
        if self._owner_loop is not None and self._owner_loop is not loop:
            raise RuntimeError("模型客户端不能跨事件循环复用；请在同一循环使用和关闭")
        self._owner_loop = loop

    @staticmethod
    def _validate_async_client(client: Any, backend: str) -> None:
        endpoint = (getattr(client, "messages", None) if backend == "anthropic"
                    else getattr(getattr(client, "chat", None), "completions", None))
        create = getattr(endpoint, "create", None)
        if not callable(create) or not inspect.iscoroutinefunction(inspect.unwrap(create)):
            raise TypeError(f"client 必须提供 {backend} SDK 的异步 create 接口")

    async def aclose(self) -> None:
        """在所属循环释放模型创建的连接；重复关闭无副作用。"""
        if self._closed:
            return
        self._check_loop()
        if self._owns_client and self._client is not None:
            await self._client.close()
        self._closed = True

    @staticmethod
    async def _close_stream(stream: Any) -> None:
        """SDK 流使用 close，原生异步生成器使用 aclose；两者都必须 await。"""
        close = getattr(stream, "close", None) or stream.aclose
        await close()

    async def __aenter__(self):
        self._check_loop()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()


class BaseChatModel(_AsyncModelLifecycle):
    """
    聊天模型基类，封装公共调用、重试与响应解析逻辑。
    """

    def __init__(
        self,
        api_base: str | None,
        api_key: str | None,
        model_name: str,
        thinking: Optional[Dict[str, Any]] = None,
        max_retry_times: int = 3,
        wait_time: float = 1,
        client: Optional[Any] = None,
    ) -> None:
        self.api_base = api_base
        self.api_key = api_key
        self.model_name = model_name
        self.thinking = self._normalize_thinking(thinking)
        self._thinking_supported = True
        self.max_retry_times = max_retry_times
        self.wait_time = wait_time
        self._owns_client = client is None
        self._client = client if client is not None else self._init_client()

    @property
    def client(self) -> Any:
        """原生异步 SDK 客户端；模型不包装或改写 SDK 接口。"""
        return self._client

    def _init_client(self) -> Any:
        raise NotImplementedError

    def _validate_messages(self, messages: Any) -> List[Dict[str, Any]]:
        if not isinstance(messages, list):
            raise TypeError("messages 必须为 list[dict]")
        cleaned_messages: List[Dict[str, Any]] = []
        for idx, item in enumerate(messages):
            if not isinstance(item, dict):
                raise TypeError(f"messages[{idx}] 必须为 dict")
            content = item.get("content")
            if isinstance(content, list):
                cleaned_blocks: list[Any] = []
                removed_indexes: list[int] = []
                for block_idx, block in enumerate(content):
                    if (
                        isinstance(block, dict)
                        and str(block.get("type") or "").strip() == "text"
                        and not str(block.get("text") or "").strip()
                    ):
                        removed_indexes.append(block_idx)
                        continue
                    cleaned_blocks.append(block)
                if not cleaned_blocks:
                    indexes = ",".join(str(value) for value in removed_indexes) or "none"
                    raise ValueError(
                        f"messages[{idx}] 在过滤空 text block 后为空；"
                        f"empty_block_indexes=[{indexes}]"
                    )
                if removed_indexes:
                    cleaned_item = dict(item)
                    cleaned_item["content"] = cleaned_blocks
                    cleaned_messages.append(cleaned_item)
                else:
                    cleaned_messages.append(item)
                continue
            if content is None or (isinstance(content, str) and not content.strip()):
                raise ValueError(
                    f"messages[{idx}].content[0] 是空 text block，整条消息为空"
                )
            cleaned_messages.append(item)
        return cleaned_messages

    def _normalize_stop_sequence(self, stop_sequence: Optional[Any]) -> Optional[List[str]]:
        if stop_sequence is None:
            return None
        if not isinstance(stop_sequence, list):
            raise TypeError("stop_sequence 必须为 list[str]")
        for idx, item in enumerate(stop_sequence):
            if not isinstance(item, str):
                raise TypeError(f"stop_sequence[{idx}] 必须为 str")
        return stop_sequence

    def _normalize_thinking(self, thinking: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        统一思考参数。

        默认策略固定为显式禁用 thinking，避免 provider 默认值变化导致行为漂移。
        同时兼容旧的 {"enabled": true/false} 形态，统一收敛为 {"type": "..."}。
        支持的 type: enabled, disabled, auto, adaptive。
        """
        if thinking is None:
            return {"type": "disabled"}
        if not isinstance(thinking, dict):
            raise TypeError("thinking 必须为 dict")

        normalized = dict(thinking)
        if "type" in normalized:
            thinking_type = str(normalized.get("type") or "").strip().lower()
            if thinking_type not in {"enabled", "disabled", "auto", "adaptive"}:
                raise ValueError(f"thinking.type 不合法: {thinking_type}")
            normalized["type"] = thinking_type
            return normalized

        if "enabled" in normalized:
            enabled = normalized.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("thinking.enabled 必须为 bool")
            return {"type": "enabled" if enabled else "disabled"}

        raise ValueError("thinking 必须包含 type 或 enabled 字段")

    def _build_payload(self, messages: List[Dict[str, Any]], stop_sequence: Optional[List[str]]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": self._prepare_messages(messages),
        }
        if stop_sequence:
            payload["stop"] = stop_sequence
        return self._inject_provider_payload_options(payload)

    @staticmethod
    def _prepare_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """在出站边界移除内部标记，并将本地图片编码为 API data URL。

        CACHE_BOUNDARY_KEY 是框架内部约定字段，OpenAI/Ark/Doubao 等接口不识别，
        需要在出站前剥离。图片使用与编辑工具共享的转换函数，HTTP(S) 链接
        原样保留，本地文件只在这里编码。按需复制被转换的层级，不改写上下文
        或嵌套 image_url，保证会话、日志和后续重试仍持有原始图片引用。
        """
        cleaned: List[Dict[str, Any]] = []
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                cleaned.append(message)
                continue
            new_content = []
            mutated = False
            for block in content:
                if isinstance(block, dict) and CACHE_BOUNDARY_KEY in block:
                    block = {k: v for k, v in block.items() if k != CACHE_BOUNDARY_KEY}
                    mutated = True
                if isinstance(block, dict) and block.get("type") == "image_url":
                    image_url = block.get("image_url")
                    if isinstance(image_url, dict) and "url" in image_url:
                        source = image_to_api_source(image_url["url"])
                        if source != image_url["url"]:
                            block = {**block, "image_url": {**image_url, "url": source}}
                            mutated = True
                new_content.append(block)
            if mutated:
                new_message = dict(message)
                new_message["content"] = new_content
                cleaned.append(new_message)
            else:
                cleaned.append(message)
        return cleaned

    def _inject_provider_payload_options(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        给请求 payload 注入 provider 特有参数。

        默认实现保持 provider 原生字段直传；需要通过 OpenAI SDK `extra_body`
        传扩展字段时由 ChatModel 覆写该方法。
        """
        if self._thinking_supported:
            payload["thinking"] = self.thinking
        return payload

    def _extract_reasoning_content(self, message: Any) -> Optional[Any]:
        return _get_field(message, "reasoning_content")

    def _build_message_from_stream_chunks(self, chunks: list[Any]) -> Dict[str, Any]:
        """Aggregate OpenAI-compatible streaming chunks into one message."""

        role = "assistant"
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: dict[str, Any] | None = None
        for chunk in chunks:
            chunk_usage = normalize_token_usage(_get_field(chunk, "usage"))
            if chunk_usage is not None:
                usage = chunk_usage
            choices = getattr(chunk, "choices", None)
            if not choices and isinstance(chunk, dict):
                choices = chunk.get("choices")
            if not choices:
                continue
            choice = choices[0]
            delta = _get_field(choice, "delta")
            if delta is None and isinstance(choice, dict):
                delta = choice.get("delta")
            if delta is None:
                continue
            delta_role = _get_field(delta, "role")
            if delta_role:
                role = str(delta_role)
            content = _get_field(delta, "content")
            if content:
                content_parts.append(str(content))
            reasoning = _get_field(delta, "reasoning_content") or _get_field(delta, "reasoning")
            if reasoning:
                reasoning_parts.append(str(reasoning))
        return {
            "role": role,
            "content": "".join(content_parts),
            "reasoning_content": "".join(reasoning_parts),
            "usage": usage,
        }

    async def _invoke_model_streaming_once(
        self, payload: Dict[str, Any], usage_calls: list[dict[str, Any]],
    ) -> Dict[str, Any]:
        """在当前协程读取整条原生流，取消后先关闭连接再交还调用方。"""
        stream_payload = {**payload, "stream": True}
        disable_usage = stream_payload.pop("_disable_stream_usage_options", False)
        if not disable_usage:
            stream_payload.setdefault("stream_options", {"include_usage": True})
        try:
            stream = await self.client.chat.completions.create(**stream_payload)
        except TypeError:
            raise
        except (Exception, asyncio.CancelledError):
            self._append_usage(usage_calls, None)
            raise
        chunks: list[Any] = []
        usage: Any = None
        try:
            async for chunk in stream:
                chunks.append(chunk)
                if _get_field(chunk, "usage") is not None:
                    usage = _get_field(chunk, "usage")
        finally:
            # asyncio 的取消只注入一次；finally 中继续等待 SDK 的关闭完成。
            try:
                await self._close_stream(stream)
            finally:
                self._append_usage(usage_calls, usage)
        return self._build_message_from_stream_chunks(chunks)

    async def _invoke_model_once(
        self, payload: Dict[str, Any], *, stream: bool,
        usage_calls: list[dict[str, Any]],
    ) -> Any:
        if stream:
            try:
                return await self._invoke_model_streaming_once(payload, usage_calls)
            except TypeError as exc:
                if "stream_options" not in str(exc):
                    raise
                logger.warning("SDK 不支持 stream_options，移除该选项后重新建立流")
                options = {**payload, "_disable_stream_usage_options": True}
                return await self._invoke_model_streaming_once(options, usage_calls)
        return await self._call_sdk_once(self.client.chat.completions.create, payload, usage_calls)

    def _append_usage(self, usage_calls: list[dict[str, Any]], usage: Any) -> None:
        """只向当前 generate 的局部列表追加用量，调用方决定如何收集与汇总。"""
        usage_calls.append({
            "backend": getattr(self, "backend", "unknown"),
            "model_name": self.model_name,
            "usage": normalize_token_usage(usage),
        })

    async def _call_sdk_once(
        self, create: Any, payload: Dict[str, Any], usage_calls: list[dict[str, Any]],
    ) -> Any:
        """网络重试交给 SDK，只记录其对外可见的一次调用。"""
        try:
            response = await create(**payload)
        except TypeError:
            raise
        except (Exception, asyncio.CancelledError):
            self._append_usage(usage_calls, None)
            raise
        self._append_usage(usage_calls, _get_field(response, "usage"))
        return response

    @staticmethod
    def _should_retry_without_thinking(exc: TypeError, payload: Dict[str, Any]) -> bool:
        """
        某些 SDK 版本会在客户端参数校验阶段直接拒绝未知字段 thinking。

        这里仅在错误信息明确指向 thinking 参数不被接受时做一次本地降级重试；
        其他 TypeError 仍按原样抛出，避免吞掉真实的调用错误。
        """
        if "thinking" not in payload:
            return False
        error_text = str(exc).lower()
        return "unexpected keyword argument" in error_text and "thinking" in error_text

    def _validate_response_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        通用响应校验入口。

        当前只落空字符串/纯空白响应校验。后续如果还要增加缺字段、结构异常等
        规则，应统一放在这里，避免把校验逻辑重新堆回 generate()。
        """
        content = message.get("content", "")
        if str(content or "").strip():
            return message
        raise _EmptyModelResponseError("模型返回了空内容")

    async def _run_generate_lifecycle(
        self, messages: List[Dict[str, Any]], stop_sequence: Optional[List[str]],
        *, stream: bool, usage_calls: list[dict[str, Any]],
    ) -> Dict[str, Any]:
        """只为空响应重试；连接、限流和超时错误由 SDK 自己处理。"""
        # 只把包含图片的出站准备移出循环；普通文本请求不额外调度工作线程。
        has_images = any(
            isinstance(block, dict) and block.get("type") == "image_url"
            for message in messages if isinstance(message.get("content"), list)
            for block in message["content"]
        )
        payload = (await asyncio.to_thread(self._build_payload, messages, stop_sequence)
                   if has_images else self._build_payload(messages, stop_sequence))
        for attempt in range(1, self.max_retry_times + 1):
            try:
                try:
                    response = await self._invoke_model_once(payload, stream=stream, usage_calls=usage_calls)
                except TypeError as exc:
                    if not self._should_retry_without_thinking(exc, payload):
                        raise
                    logger.warning("%s SDK 不支持 thinking，移除后重试", self.__class__.__name__)
                    self._thinking_supported = False
                    payload = dict(payload)
                    payload.pop("thinking", None)
                    response = await self._invoke_model_once(payload, stream=stream, usage_calls=usage_calls)
                message = response if isinstance(response, dict) else self._build_message(response)
                return {**self._validate_response_message(message), "usage_calls": usage_calls}
            except _EmptyModelResponseError:
                if attempt == self.max_retry_times:
                    logger.error("%s 连续 %s 次返回空内容", self.__class__.__name__, attempt)
                    raise
                logger.warning("%s 返回空内容，重试中 (%s/%s)", self.__class__.__name__, attempt, self.max_retry_times)
                await asyncio.sleep(self.wait_time)
        raise AssertionError("max_retry_times 必须为正数")

    def _build_message(self, response: Any) -> Dict[str, Any]:
        choices = getattr(response, "choices", None)
        if not choices:
            raise ValueError("返回结果缺少 choices")

        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None and isinstance(choice, dict):
            message = choice.get("message")
        if message is None:
            raise ValueError("返回结果缺少 message")

        role = _get_field(message, "role") or "assistant"
        content = _get_field(message, "content") or ""
        reasoning_content = self._extract_reasoning_content(message)

        return {
            "role": role,
            "content": content,
            "reasoning_content": "" if reasoning_content is None else str(reasoning_content),
            "usage": normalize_token_usage(_get_field(response, "usage")),
        }

    async def generate(
        self, messages: List[Dict[str, Any]], stop_sequence: Optional[List[str]] = None,
        *, stream: bool = False,
    ) -> Dict[str, Any]:
        """生成完整结果；stream 只控制 SDK 传输，用任务取消中断本次调用。"""
        usage_calls: list[dict[str, Any]] = []
        try:
            self._check_loop()
            messages = self._validate_messages(messages)
            stop_sequence = self._normalize_stop_sequence(stop_sequence)
            return await self._run_generate_lifecycle(
                messages, stop_sequence, stream=stream, usage_calls=usage_calls,
            )
        except (Exception, asyncio.CancelledError) as exc:
            exc.usage_calls = usage_calls
            if isinstance(exc, asyncio.CancelledError):
                logger.info("聊天模型调用已取消: model=%s", self.model_name)
            else:
                logger.error("聊天模型调用失败: model=%s error=%s", self.model_name, exc)
            raise


class ChatModel(BaseChatModel):
    """统一聊天模型：backend 选择原生 SDK，共享生成、重试和取消生命周期。

    连接参数可直接传入，也可注入与 backend 对应的原生 client。OpenAI/Ark
    共用 Chat Completions 协议；Anthropic 的 system、缓存、thinking 和流式
    事件在私有转换方法中处理，不向调用方暴露不同的模型类型。
    """

    # Anthropic 高 effort 需要足够输出空间，沿用原有 token 下限。
    _EFFORT_MIN_MAX_TOKENS: Dict[str, int] = {
        "low": 4096,
        "medium": 8192,
        "high": 16000,
        "xhigh": 64000,
        "max": 64000,
    }
    # 部分 Opus 在当前 SDK 中只允许最多 8192 个非流式输出 token；采用该
    # 保守阈值也覆盖通用的长请求限制，不依赖 SDK 私有模型表或超时计算器。
    _ANTHROPIC_STREAM_TOKEN_THRESHOLD = 8192

    def __init__(
        self,
        model_name: str,
        *,
        backend: str = "openai",
        api_base: str | None = None,
        api_key: str | None = None,
        thinking: Optional[Dict[str, Any]] = None,
        max_retry_times: int = 3,
        wait_time: float = 1,
        client: Optional[Any] = None,
        max_tokens: int = 4096,
        effort_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("聊天模型名称必须为非空字符串")
        if not isinstance(backend, str) or backend not in {"openai", "doubao", "anthropic"}:
            raise ValueError("聊天模型 backend 必须为 openai、doubao 或 anthropic")
        for name, value in (("max_retry_times", max_retry_times), ("max_tokens", max_tokens)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} 必须为正整数")
        if (
            isinstance(wait_time, bool)
            or not isinstance(wait_time, (int, float))
            or not math.isfinite(wait_time)
            or wait_time < 0
        ):
            raise ValueError("wait_time 必须为非负有限数值")
        if effort_config is not None and not isinstance(effort_config, dict):
            raise TypeError("effort_config 必须为 dict")
        self.backend = backend
        self.max_tokens = max_tokens
        self.effort_config = dict(effort_config) if effort_config is not None else None
        if client is not None:
            self._validate_client(client)
        super().__init__(
            api_base=api_base,
            api_key=api_key,
            model_name=model_name.strip(),
            thinking=thinking,
            max_retry_times=max_retry_times,
            wait_time=wait_time,
            client=client,
        )

    def _validate_client(self, client: Any) -> None:
        self._validate_async_client(client, self.backend)

    def _init_client(self) -> Any:
        """仅加载选中的 SDK；注入 client 时不读取凭证或创建新连接。"""
        options: Dict[str, Any] = {}
        if self.api_base is not None:
            options["base_url"] = self.api_base
        if self.api_key is not None:
            # 保留项目现有的 Anthropic bearer token 约定；其他认证可注入 client。
            options["auth_token" if self.backend == "anthropic" else "api_key"] = self.api_key
        if self.backend == "openai":
            from openai import AsyncOpenAI

            client = AsyncOpenAI(**options)
        elif self.backend == "doubao":
            from volcenginesdkarkruntime import AsyncArk

            client = AsyncArk(**options)
        else:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(**options)
        self._validate_client(client)
        logger.debug("创建聊天模型客户端: backend=%s model=%s", self.backend, self.model_name)
        return client

    def _inject_provider_payload_options(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """OpenAI SDK 扩展参数放入 extra_body，Ark 使用原生 thinking 字段。"""
        if self.backend == "openai":
            extra_body = dict(payload.get("extra_body") or {})
            extra_body["thinking"] = self.thinking
            payload["extra_body"] = extra_body
            return payload
        return super()._inject_provider_payload_options(payload)

    def _extract_reasoning_content(self, message: Any) -> Optional[Any]:
        reasoning = _get_field(message, "reasoning_content")
        if self.backend == "openai":
            return reasoning or _get_field(message, "reasoning")
        return reasoning

    def _build_payload(
        self, messages: List[Dict[str, Any]], stop_sequence: Optional[List[str]]
    ) -> Dict[str, Any]:
        if self.backend == "anthropic":
            return self._build_anthropic_payload(messages, stop_sequence)
        return super()._build_payload(messages, stop_sequence)

    async def _invoke_model_once(
        self, payload: Dict[str, Any], *, stream: bool,
        usage_calls: list[dict[str, Any]],
    ) -> Any:
        if self.backend == "anthropic":
            return await self._invoke_anthropic_once(payload, stream=stream, usage_calls=usage_calls)
        return await super()._invoke_model_once(payload, stream=stream, usage_calls=usage_calls)

    def _build_message(self, response: Any) -> Dict[str, Any]:
        if self.backend == "anthropic":
            return self._build_anthropic_message(response)
        return super()._build_message(response)

    def _content_to_text(self, content: Any) -> str:
        """把框架内部 content block 收敛为 Anthropic system 可接受的纯文本。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                else:
                    parts.append(str(block))
            return "".join(parts)
        return str(content or "")

    def _content_to_anthropic(self, content: Any) -> Any:
        """
        转为 Anthropic messages.content。

        文本块保持原生 {"type": "text", "text": "..."}；非 Anthropic 原生块
        先转成文本，避免把 OpenAI/Ark 专属 image_url 结构直接交给 Claude SDK。
        """
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content or "")

        blocks: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                blocks.append({"type": "text", "text": str(block.get("text") or "")})
            elif isinstance(block, dict) and block.get("type") == "image":
                blocks.append(dict(block))
            else:
                blocks.append({"type": "text", "text": str(block)})
        return blocks

    def _system_blocks_to_anthropic(self, content: Any) -> Any:
        """把 system content 转为 Anthropic system 字段。

        - 纯字符串 / 单文本块：返回字符串，保持与历史 payload 完全一致；
        - 含缓存边界标记的多块：返回 Anthropic system blocks 列表，并在被标记的
          block 上注入 cache_control={"type": "ephemeral"}，开启 prompt 缓存。
          标记字段本身不会出现在出站 payload 中（只翻译，不透传）。
        """
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content or "")

        has_boundary = any(
            isinstance(block, dict) and block.get(CACHE_BOUNDARY_KEY) for block in content
        )
        if not has_boundary:
            return self._content_to_text(content)

        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                blocks.append({"type": "text", "text": str(block)})
                continue
            text = str(block.get("text") or "")
            if not text:
                continue
            out_block: dict[str, Any] = {"type": "text", "text": text}
            if block.get(CACHE_BOUNDARY_KEY):
                out_block["cache_control"] = {"type": "ephemeral"}
            blocks.append(out_block)
        return blocks

    def _build_anthropic_payload(
        self, messages: List[Dict[str, Any]], stop_sequence: Optional[List[str]]
    ) -> Dict[str, Any]:
        system_field: Any = None
        anthropic_messages: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "user").strip().lower()
            content = message.get("content", "")
            if role == "system":
                system_field = self._merge_system_field(
                    system_field, self._system_blocks_to_anthropic(content)
                )
                continue
            anthropic_messages.append(
                {
                    "role": "assistant" if role == "assistant" else "user",
                    "content": self._content_to_anthropic(content),
                }
            )

        # 根据 effort 级别自动提升 max_tokens
        max_tokens = self.max_tokens
        if self.effort_config:
            effort_level = self.effort_config.get("effort", "")
            min_tokens = self._EFFORT_MIN_MAX_TOKENS.get(effort_level, 0)
            if min_tokens and max_tokens < min_tokens:
                max_tokens = min_tokens

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": max_tokens,
            "messages": anthropic_messages,
        }
        if system_field:
            payload["system"] = system_field
        if stop_sequence:
            payload["stop_sequences"] = stop_sequence
        # thinking: adaptive 模式（disabled 时不注入）
        if self._thinking_supported and self.thinking.get("type") != "disabled":
            payload["thinking"] = self.thinking
        # effort 通过 output_config 传递（Anthropic 官方推荐方式）
        if self.effort_config:
            payload["output_config"] = self.effort_config
        return payload

    @staticmethod
    def _merge_system_field(existing: Any, addition: Any) -> Any:
        """合并多个 system 消息。

        通常只有一个 system 消息；存在多个时按出现顺序拼接。一旦任一侧是
        block 列表（带 cache_control），结果统一收敛为 block 列表以保留缓存语义。
        """
        if addition is None or addition == "":
            return existing
        if existing is None or existing == "":
            return addition
        existing_blocks = existing if isinstance(existing, list) else [{"type": "text", "text": str(existing)}]
        addition_blocks = addition if isinstance(addition, list) else [{"type": "text", "text": str(addition)}]
        return [*existing_blocks, *addition_blocks]

    async def _invoke_anthropic_streaming_once(
        self, payload: Dict[str, Any], usage_calls: list[dict[str, Any]],
    ) -> Dict[str, Any]:
        """用原生异步上下文管理器读流，合并分阶段上报的 usage。"""
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        raw_usage: dict[str, Any] = {}
        entered = False

        def merge_usage(source: Any) -> None:
            normalized = normalize_token_usage(source)
            if normalized is not None:
                raw_usage.update(normalized["provider_usage"])

        try:
            async with self.client.messages.stream(**payload) as response:
                entered = True
                async for event in response:
                    kind = _get_field(event, "type")
                    if kind == "message_start":
                        merge_usage(_get_field(_get_field(event, "message"), "usage"))
                    elif kind == "message_delta":
                        merge_usage(_get_field(event, "usage"))
                    elif kind == "content_block_delta":
                        delta = _get_field(event, "delta")
                        if _get_field(delta, "type") == "text_delta":
                            text_parts.append(str(_get_field(delta, "text") or ""))
                        elif _get_field(delta, "type") == "thinking_delta":
                            thinking_parts.append(str(_get_field(delta, "thinking") or ""))
                merge_usage(_get_field(await response.get_final_message(), "usage"))
        except TypeError:
            # 本地参数校验失败不产生一条计费请求记录。
            if entered:
                self._append_usage(usage_calls, raw_usage or None)
            raise
        except (Exception, asyncio.CancelledError):
            self._append_usage(usage_calls, raw_usage or None)
            raise
        else:
            self._append_usage(usage_calls, raw_usage or None)
        return {
            "role": "assistant", "content": "".join(text_parts),
            "reasoning_content": "".join(thinking_parts),
            "usage": normalize_token_usage(raw_usage) if raw_usage else None,
        }

    async def _invoke_anthropic_once(
        self, payload: Dict[str, Any], *, stream: bool,
        usage_calls: list[dict[str, Any]],
    ) -> Any:
        if stream or payload["max_tokens"] > self._ANTHROPIC_STREAM_TOKEN_THRESHOLD:
            return await self._invoke_anthropic_streaming_once(payload, usage_calls)
        return await self._call_sdk_once(self.client.messages.create, payload, usage_calls)

    def _build_anthropic_message(self, response: Any) -> Dict[str, Any]:
        blocks = _get_field(response, "content")
        if not blocks:
            raise ValueError("返回结果缺少 content")

        parts: list[str] = []
        reasoning_parts: list[str] = []
        for block in blocks:
            block_type = str(_get_field(block, "type") or "").strip().lower()
            if block_type == "text":
                parts.append(str(_get_field(block, "text") or ""))
            elif block_type == "thinking":
                reasoning_parts.append(
                    str(_get_field(block, "thinking") or _get_field(block, "text") or "")
                )

        return {
            "role": _get_field(response, "role") or "assistant",
            "content": "".join(parts),
            "reasoning_content": "\n".join(part for part in reasoning_parts if part),
            "usage": normalize_token_usage(_get_field(response, "usage")),
        }



__all__ = ["BaseChatModel", "ChatModel", "normalize_token_usage"]
