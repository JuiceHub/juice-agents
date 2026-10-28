"""聊天模型：统一 generate 接口及各 provider 的请求、重试与响应解析。"""

import logging
import math
import time
import threading
from typing import Any, Dict, List, Optional

from juice_agents.core.runner.execution.cancellation import StreamCancelled, raise_if_cancelled
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

    该异常只用于把“空响应”纳入与网络抖动同一层的重试循环，
    最终如何提示给 agent 由上层协议层决定。
    """


class BaseChatModel:
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
        self.client = client if client is not None else self._init_client()

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
            "messages": self._strip_cache_boundary(messages),
        }
        if stop_sequence:
            payload["stop"] = stop_sequence
        return self._inject_provider_payload_options(payload)

    @staticmethod
    def _strip_cache_boundary(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """剥离框架内部的缓存边界标记，得到 provider 可直接消费的 messages。

        CACHE_BOUNDARY_KEY 是框架内部约定字段，OpenAI/Ark/Doubao 等接口不识别，
        若原样发送可能触发参数校验错误。不识别缓存协议的 provider 直接丢弃该标记，
        缓存语义随之退化为无缓存——功能正确，只是少了一层成本优化。
        仅在标记存在时做浅拷贝，避免给常规消息引入额外开销。
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

    def _is_retryable_exception(self, exc: Exception) -> bool:
        """只重试可恢复的模型/传输失败，不从错误正文猜测 HTTP 状态。"""
        if isinstance(exc, (StreamCancelled, TypeError, ValueError, AssertionError)):
            return False
        if isinstance(exc, _EmptyModelResponseError):
            return True

        # 三个 SDK 都暴露 status_code；原生 HTTP 异常可从 response 读取。
        # 状态优先于正文，避免 400/401 的说明包含 timeout 等词时重复请求。
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status, int) and not isinstance(status, bool):
            return status in {408, 409, 429} or 500 <= status < 600
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return True

        # SDK 的连接/超时异常不继承内建 ConnectionError；只加载当前 backend，
        # 保持模型模块冷导入不加载其他 provider 的约定。未知异常直接交给上层。
        backend = getattr(self, "backend", None)
        if backend == "openai":
            from openai import APIConnectionError
        elif backend == "anthropic":
            from anthropic import APIConnectionError
        elif backend == "doubao":
            from volcenginesdkarkruntime._exceptions import ArkAPIConnectionError as APIConnectionError
        else:
            return False
        return isinstance(exc, APIConnectionError)

    def _extract_reasoning_content(self, message: Any) -> Optional[Any]:
        return _get_field(message, "reasoning_content")

    def _close_stream_quietly(self, stream: Any) -> None:
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("failed to close model stream", exc_info=True)

    def _watch_stream_cancel(self, stream: Any, cancel_event: threading.Event) -> Any:
        """Close a provider stream as soon as the runner cancel token is set."""

        done = threading.Event()

        def _watch() -> None:
            while not done.is_set():
                if cancel_event.wait(0.05):
                    if not done.is_set():
                        self._close_stream_quietly(stream)
                    return

        thread = threading.Thread(target=_watch, name="model-stream-cancel-watch", daemon=True)
        thread.start()
        return done.set

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

    def _invoke_model_streaming_once(
        self, payload: Dict[str, Any], cancel_event: threading.Event,
        usage_calls: list[dict[str, Any]],
    ) -> Dict[str, Any]:
        """Call OpenAI-compatible chat completions in streaming mode."""

        stream_payload = dict(payload)
        stream_payload["stream"] = True
        disable_usage_options = bool(stream_payload.pop("_disable_stream_usage_options", False))
        if disable_usage_options:
            stream_payload.pop("stream_options", None)
        elif stream_payload.get("stream_options") is None:
            stream_payload.setdefault("stream_options", {"include_usage": True})
        try:
            stream = self.client.chat.completions.create(**stream_payload)
        except TypeError:
            # SDK 本地参数校验失败时尚未发送请求，由外层执行协议降级。
            raise
        except Exception:
            self._append_usage(usage_calls, None)
            raise
        stop_watching = self._watch_stream_cancel(stream, cancel_event)
        chunks: list[Any] = []
        usage: Any = None
        try:
            for chunk in stream:
                # 已到达的 chunk 即使紧接着取消，也可能包含已发生的消耗。
                chunks.append(chunk)
                chunk_usage = _get_field(chunk, "usage")
                if chunk_usage is not None:
                    usage = chunk_usage
                raise_if_cancelled(cancel_event)
        except StreamCancelled:
            self._close_stream_quietly(stream)
            raise
        finally:
            stop_watching()
            self._append_usage(usage_calls, usage)
        raise_if_cancelled(cancel_event)
        return self._build_message_from_stream_chunks(chunks)

    def _invoke_model_once(
        self, payload: Dict[str, Any], cancel_event: threading.Event | None = None,
        *, usage_calls: list[dict[str, Any]],
    ) -> Any:
        """
        执行一次底层 SDK 调用。

        生命周期编排放在基类中，ChatModel 只在 SDK 边界分发 provider 协议，
        不重复实现重试循环。
        """
        if cancel_event is not None:
            raise_if_cancelled(cancel_event)
            try:
                return self._invoke_model_streaming_once(payload, cancel_event, usage_calls)
            except TypeError as exc:
                if "stream_options" in str(exc):
                    logger.warning(
                        "%s 客户端不支持 streaming usage 选项，降级为不带 stream_options",
                        self.__class__.__name__,
                    )
                    streaming_payload = dict(payload)
                    streaming_payload.pop("stream_options", None)
                    streaming_payload["_disable_stream_usage_options"] = True
                    try:
                        return self._invoke_model_streaming_once(streaming_payload, cancel_event, usage_calls)
                    except TypeError as fallback_exc:
                        exc = fallback_exc
                logger.debug(
                    "%s streaming cancellation unsupported by client, falling back to blocking call: %s",
                    self.__class__.__name__,
                    exc,
                )
        try:
            return self._call_sdk_once(self.client.chat.completions.create, payload, usage_calls)
        except TypeError as exc:
            if self._should_retry_without_thinking(exc, payload):
                logger.warning(
                    "%s 客户端不支持 thinking 参数，自动降级为不传 thinking",
                    self.__class__.__name__,
                )
                self._thinking_supported = False
                fallback_payload = dict(payload)
                fallback_payload.pop("thinking", None)
                return self._call_sdk_once(self.client.chat.completions.create, fallback_payload, usage_calls)
            raise

    def _append_usage(self, usage_calls: list[dict[str, Any]], usage: Any) -> None:
        """只向当前 generate 的局部列表追加用量，调用方决定如何收集与汇总。"""
        usage_calls.append({
            "backend": getattr(self, "backend", "unknown"),
            "model_name": self.model_name,
            "usage": normalize_token_usage(usage),
        })

    def _call_sdk_once(
        self, create: Any, payload: Dict[str, Any], usage_calls: list[dict[str, Any]],
    ) -> Any:
        """记录响应及网络失败；解析失败或空内容重试也保留已发生的消耗。"""
        try:
            response = create(**payload)
        except TypeError:
            # thinking/stream 兼容性降级是在 SDK 本地完成，不当作计费请求。
            raise
        except Exception:
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

    def _wait_before_retry(self, cancel_event: threading.Event | None) -> None:
        if cancel_event is not None:
            if cancel_event.wait(float(self.wait_time)):
                raise StreamCancelled()
            return
        time.sleep(self.wait_time)

    def _handle_generate_exception(
        self, exc: Exception, attempt: int, cancel_event: threading.Event | None = None
    ) -> None:
        """
        统一处理生命周期中的异常分流。

        这里负责判断是否可重试、输出日志，以及在需要时等待后重试。
        如果异常不该重试或已经达到最大重试次数，会直接重新抛出异常。
        """
        if isinstance(exc, StreamCancelled):
            logger.info("%s 生成被用户中断：%s", self.__class__.__name__, exc.reason)
            raise

        if not self._is_retryable_exception(exc):
            logger.error(
                "%s 生成失败：%s",
                self.__class__.__name__,
                exc,
            )
            raise

        if isinstance(exc, _EmptyModelResponseError):
            if attempt >= self.max_retry_times:
                logger.error(
                    "%s 连续返回空内容，已达到最大重试次数 (%s/%s)",
                    self.__class__.__name__,
                    attempt,
                    self.max_retry_times,
                )
                raise _EmptyModelResponseError(
                    f"{self.__class__.__name__} 连续 {self.max_retry_times} 次返回空内容"
                ) from exc

            logger.warning(
                "%s 返回空内容，重试中 (%s/%s)",
                self.__class__.__name__,
                attempt,
                self.max_retry_times,
            )
            self._wait_before_retry(cancel_event)
            return

        logger.warning(
            "%s 生成失败，重试中 (%s/%s): %s",
            self.__class__.__name__,
            attempt,
            self.max_retry_times,
            exc,
        )
        if attempt >= self.max_retry_times:
            raise
        self._wait_before_retry(cancel_event)

    def _run_generate_lifecycle(
        self,
        messages: List[Dict[str, Any]],
        stop_sequence: Optional[List[str]],
        cancel_event: threading.Event | None = None,
        *, usage_calls: list[dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        显式串联一次 generate 的生命周期。

        流程固定为：构造请求 -> 调用模型 -> 解析响应 -> 校验响应。
        发生异常时统一交给 _handle_generate_exception 做重试决策。
        """
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retry_times + 1):
            try:
                raise_if_cancelled(cancel_event)
                payload = self._build_payload(messages, stop_sequence)
                response = self._invoke_model_once(payload, cancel_event=cancel_event, usage_calls=usage_calls)
                message = response if isinstance(response, dict) else self._build_message(response)
                raise_if_cancelled(cancel_event)
                message = self._validate_response_message(message)
                return {**message, "usage_calls": usage_calls}
            except Exception as exc:  # pragma: no cover - runtime branch
                last_err = exc
                self._handle_generate_exception(exc, attempt, cancel_event)

        if last_err:
            raise last_err
        raise RuntimeError(f"{self.__class__.__name__} 未知错误")

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

    def generate(
        self,
        messages: List[Dict[str, Any]],
        stop_sequence: Optional[List[str]] = None,
        *,
        cancel_event: threading.Event | None = None,
    ) -> Dict[str, Any]:
        """
        generate() 只负责输入归一化和生命周期编排。

        真正的调用阶段拆分到私有生命周期函数中，便于后续继续扩展公共校验和
        provider 复用逻辑。
        """
        usage_calls: list[dict[str, Any]] = []
        try:
            messages = self._validate_messages(messages)
            stop_sequence = self._normalize_stop_sequence(stop_sequence)
            return self._run_generate_lifecycle(
                messages, stop_sequence, cancel_event, usage_calls=usage_calls,
            )
        except Exception as exc:
            # 保留原异常类型，调用方即使在取消或失败时也能显式读取已发生的消耗。
            exc.usage_calls = usage_calls
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
        """在创建连接时检查 SDK 入口，避免错误 client 到请求时才失败。"""
        if self.backend == "anthropic":
            endpoint = getattr(client, "messages", None)
        else:
            endpoint = getattr(getattr(client, "chat", None), "completions", None)
        if not callable(getattr(endpoint, "create", None)):
            raise TypeError(f"client 必须提供 {self.backend} SDK 的可调用 create 接口")

    def _init_client(self) -> Any:
        """仅加载选中的 SDK；注入 client 时不读取凭证或创建新连接。"""
        options: Dict[str, Any] = {}
        if self.api_base is not None:
            options["base_url"] = self.api_base
        if self.api_key is not None:
            # 保留项目现有的 Anthropic bearer token 约定；其他认证可注入 client。
            options["auth_token" if self.backend == "anthropic" else "api_key"] = self.api_key
        if self.backend == "openai":
            from openai import OpenAI

            client = OpenAI(**options)
        elif self.backend == "doubao":
            from volcenginesdkarkruntime import Ark

            client = Ark(**options)
        else:
            from anthropic import Anthropic

            client = Anthropic(**options)
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

    def _invoke_model_once(
        self, payload: Dict[str, Any], cancel_event: threading.Event | None = None,
        *, usage_calls: list[dict[str, Any]],
    ) -> Any:
        if self.backend == "anthropic":
            return self._invoke_anthropic_once(payload, cancel_event, usage_calls=usage_calls)
        return super()._invoke_model_once(payload, cancel_event, usage_calls=usage_calls)

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

    def _invoke_anthropic_streaming_once(
        self, stream_method: Any, payload: Dict[str, Any],
        cancel_event: threading.Event | None, usage_calls: list[dict[str, Any]],
    ) -> Dict[str, Any]:
        """读取原生事件或官方 SDK 最终消息的 usage，快照字段覆盖而不相加。"""
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        raw_usage: dict[str, Any] = {}
        stream_cm = stream = None
        stop_watching = None
        request_started = False

        def merge_usage(usage: Any) -> None:
            normalized = normalize_token_usage(usage)
            if normalized is not None and isinstance(normalized["provider_usage"], dict):
                raw_usage.update(normalized["provider_usage"])

        try:
            stream_cm = stream_method(**payload)
            request_started = True
            with stream_cm as stream:
                # 高输出预算即使没有取消令牌也必须流式；只有可取消请求需要
                # 后台 watcher，事件聚合和用量记录始终复用同一条执行路径。
                if cancel_event is not None:
                    stop_watching = self._watch_stream_cancel(stream, cancel_event)
                for event in stream:
                    event_type = _get_field(event, "type")
                    if event_type == "message_start":
                        merge_usage(_get_field(_get_field(event, "message"), "usage"))
                    elif event_type == "message_delta":
                        merge_usage(_get_field(event, "usage"))
                    # 取消发生时仍保留刚到达的 usage，不读取 final message 阻塞退出。
                    raise_if_cancelled(cancel_event)
                    if event_type == "content_block_delta":
                        delta = _get_field(event, "delta")
                        delta_type = _get_field(delta, "type")
                        if delta_type == "text_delta":
                            text_parts.append(str(_get_field(delta, "text") or ""))
                        elif delta_type == "thinking_delta":
                            thinking_parts.append(str(_get_field(delta, "thinking") or ""))
                raise_if_cancelled(cancel_event)
                get_final_message = getattr(stream, "get_final_message", None)
                if callable(get_final_message):
                    merge_usage(_get_field(get_final_message(), "usage"))
            return {
                "role": "assistant", "content": "".join(text_parts),
                "reasoning_content": "".join(thinking_parts),
                "usage": normalize_token_usage(raw_usage) if raw_usage else None,
            }
        except StreamCancelled:
            self._close_stream_quietly(stream if stream is not None else stream_cm)
            raise
        except TypeError:
            # SDK 在创建/进入流时拒绝参数，不是一次真实模型请求。
            if stream is None:
                request_started = False
            raise
        except Exception:
            request_started = True
            raise
        finally:
            if stop_watching is not None:
                stop_watching()
            if request_started:
                self._append_usage(usage_calls, raw_usage if raw_usage else None)

    def _invoke_anthropic_once(
        self, payload: Dict[str, Any], cancel_event: threading.Event | None = None,
        *, usage_calls: list[dict[str, Any]],
    ) -> Any:
        raise_if_cancelled(cancel_event)
        if cancel_event is not None or payload["max_tokens"] > self._ANTHROPIC_STREAM_TOKEN_THRESHOLD:
            stream_method = getattr(self.client.messages, "stream", None)
            if callable(stream_method):
                try:
                    return self._invoke_anthropic_streaming_once(
                        stream_method, payload, cancel_event, usage_calls,
                    )
                except TypeError as exc:
                    if self._should_retry_without_thinking(exc, payload):
                        logger.warning("%s 客户端不支持 thinking 参数，自动降级", self.__class__.__name__)
                        self._thinking_supported = False
                        fallback_payload = dict(payload)
                        fallback_payload.pop("thinking", None)
                        return self._invoke_anthropic_once(
                            fallback_payload, cancel_event, usage_calls=usage_calls,
                        )
                    logger.debug("%s streaming unsupported, falling back to blocking call", self.__class__.__name__)
        try:
            return self._call_sdk_once(self.client.messages.create, payload, usage_calls)
        except TypeError as exc:
            if self._should_retry_without_thinking(exc, payload):
                logger.warning("%s 客户端不支持 thinking 参数，自动降级", self.__class__.__name__)
                self._thinking_supported = False
                fallback_payload = dict(payload)
                fallback_payload.pop("thinking", None)
                return self._call_sdk_once(self.client.messages.create, fallback_payload, usage_calls)
            raise

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
