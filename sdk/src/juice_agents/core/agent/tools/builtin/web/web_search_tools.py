"""
Web 工具模块，为智能体提供网页搜索能力。

当前实现：
- web_search: engine 在初始化时配置（目前内置 duckduckgo/ddg/bing），并支持最大返回数量 max_results。
- api_web_search: 基于需要 API key 的搜索引擎（当前实现 Tavily / SerpAPI），支持设置 engine 与 max_results。

浏览器导航、截图、交互、tab 等工具位于同功能域的 ``browser`` 子包。
"""

from __future__ import annotations

import json
import logging
import platform
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from juice_agents.core.config.runtime_config import (
    DEFAULT_DOTENV_PATH,
    DEFAULT_RUNTIME_CONFIG_PATH,
    get_runtime_mapping,
    normalize_runtime_key,
    resolve_env_value,
)
from juice_agents.core.utils import load_env_file

from ...runtime.base_tools import LIST_OBSERVATION_CHARS, Tool

logger = logging.getLogger(__name__)


def _parse_version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for item in (version or "").split("."):
        if not item.isdigit():
            break
        parts.append(int(item))
    return tuple(parts)


def _format_playwright_start_error(
    exc: Exception,
    *,
    libc_info: tuple[str, str] | None = None,
) -> str:
    """
    统一格式化 Playwright 启动失败信息，提供可执行的环境修复建议。
    """
    libc_name, libc_ver = libc_info or platform.libc_ver()
    libc_name = (libc_name or "").strip().lower()
    libc_ver = (libc_ver or "").strip()
    original_msg = str(exc) or exc.__class__.__name__

    exc_text = original_msg.lower()
    hints: list[str] = []
    if "asyncio loop" in exc_text or "sync api inside the asyncio" in exc_text:
        hints.append("检测到 sync Playwright 在 asyncio 事件循环线程中启动。")
        hints.append("请确认 gateway 已重启到最新版；浏览器 session 创建和操作必须通过 owner 线程队列执行。")
    else:
        hints.append("请确认已安装并初始化 Playwright: pip install playwright && playwright install chromium")
    if libc_name == "glibc" and libc_ver:
        current = _parse_version_tuple(libc_ver)
        required = _parse_version_tuple("2.27")
        if current and required and current < required:
            hints.append(f"检测到系统 glibc={libc_ver}，可能低于 Playwright driver 所需版本（通常 >= 2.27）。")
            hints.append("可选方案：升级系统 glibc/OS，或在兼容环境（容器/新镜像）中运行浏览器工具。")
    if "PlaywrightContextManager" in original_msg and "_playwright" in original_msg:
        hints.append("该 AttributeError 通常是底层 driver 进程启动失败后的连带错误。")

    return "Playwright 启动失败。可能是依赖或系统运行时不兼容。\n" + "\n".join(
        f"- {hint}" for hint in hints
    )


def _normalize_url(url: str) -> str:
    """
    规范化 URL：
    - 处理 //example.com 形式
    - 处理 /path 形式（按 duckduckgo 域名补全）
    """
    if not isinstance(url, str):
        return ""
    u = url.strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return "https://duckduckgo.com" + u
    return u


def _extract_uddg_target(url: str) -> str:
    """
    DuckDuckGo 部分结果会以 /l/?uddg=... 形式跳转，这里尝试还原真实目标链接。
    """
    u = _normalize_url(url)
    if not u:
        return ""
    try:
        parsed = urlparse(u)
        qs = parse_qs(parsed.query or "")
        if "uddg" in qs and qs["uddg"]:
            return unquote(str(qs["uddg"][0]))
    except Exception:
        return u
    return u


def _as_clean_text(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _truncate_text(value: str, max_len: int = 240) -> str:
    text = _as_clean_text(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _validate_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必须为非空字符串")
    return query.strip()


def _resolve_max_results(
    *,
    max_results: int | None,
    default_value: int,
    cap_value: int,
    logger_name: str,
) -> int:
    mr = default_value if max_results is None else int(max_results)
    if mr <= 0:
        raise ValueError("max_results 必须为正整数")
    if cap_value > 0 and mr > cap_value:
        logger.warning("%s: max_results=%d 超过 cap=%d，将被截断", logger_name, mr, cap_value)
        mr = cap_value
    return mr


def _normalize_snippet(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(x) for x in value if x is not None)
    return _as_clean_text(str(value or ""))


def _build_result(title: Any, url: Any, snippet: Any) -> Dict[str, str]:
    return {
        "title": _as_clean_text(str(title or "")),
        "url": _as_clean_text(str(url or "")),
        "snippet": _normalize_snippet(snippet),
    }


def _collect_results(
    items: list[Any],
    *,
    title_keys: tuple[str, ...],
    url_keys: tuple[str, ...],
    snippet_keys: tuple[str, ...],
    max_results: int,
) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        title = ""
        for k in title_keys:
            title = item.get(k, "") or ""
            if title:
                break

        link = ""
        for k in url_keys:
            link = item.get(k, "") or ""
            if link:
                break

        snippet: Any = ""
        for k in snippet_keys:
            snippet = item.get(k, "") or ""
            if snippet:
                break

        result = _build_result(title, link, snippet)
        if result["title"] and result["url"]:
            out.append(result)
        if len(out) >= max_results:
            break
    return out


@dataclass
class _SearchResult:
    title: str = ""
    url: str = ""
    snippet: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "title": _as_clean_text(self.title),
            "url": _as_clean_text(self.url),
            "snippet": _as_clean_text(self.snippet),
        }


class _DuckDuckGoHTMLParser(HTMLParser):
    """
    解析 duckduckgo HTML 搜索结果页（/html/）的最小解析器。

    目标：
    - 提取每条结果的 title / url / snippet
    - 尽量对 HTML 结构变化保持鲁棒
    """

    def __init__(self, max_results: int):
        super().__init__(convert_charrefs=True)
        self.max_results = max(1, int(max_results))

        self._results: List[_SearchResult] = []
        self._current: Optional[_SearchResult] = None

        self._in_title = False
        self._in_snippet = False
        # 当已拿到 max_results 且检测到下一条结果开始时，置为 True，避免把后续内容误拼到最后一条结果上
        self._done = False

    @property
    def results(self) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for r in self._results:
            d = r.to_dict()
            # 过滤掉异常空项
            if d.get("title") and d.get("url"):
                out.append(d)
        return out[: self.max_results]

    def _has_class(self, attrs: Dict[str, str], class_name: str) -> bool:
        cls = attrs.get("class", "") or ""
        # class 可能是多个空格分隔
        return class_name in cls.split()

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]):
        if self._done:
            return

        attrs_dict = {k: (v or "") for k, v in attrs}

        # 结果标题链接
        if tag == "a" and ("result__a" in (attrs_dict.get("class") or "")):
            # 已达到最大结果数：如果后续又出现新的 result__a，说明是第 N+1 条结果的开始，
            # 为避免误把其 snippet/文本拼到第 N 条上，直接停止解析。
            if len(self._results) >= self.max_results:
                self._done = True
                self._current = None
                self._in_title = False
                self._in_snippet = False
                return
            href = attrs_dict.get("href", "") or ""
            self._current = _SearchResult(url=_extract_uddg_target(href))
            self._results.append(self._current)
            self._in_title = True
            return

        # 结果摘要（有时为 div，有时为 a/span 等容器）
        if "result__snippet" in (attrs_dict.get("class") or "") and self._current is not None:
            self._in_snippet = True
            return

    def handle_endtag(self, tag: str):
        if tag == "a" and self._in_title:
            self._in_title = False
            return
        # snippet 可能由多种 tag 关闭，这里只要进入过 snippet 区域，遇到常见容器关闭就退出
        if self._in_snippet and tag in ("div", "a", "span"):
            self._in_snippet = False
            return

    def handle_data(self, data: str):
        if self._done:
            return
        if self._current is None:
            return
        if self._in_title:
            self._current.title += data
            return
        if self._in_snippet:
            self._current.snippet += data
            return


def _duckduckgo_search_html(query: str, *, max_results: int, timeout_s: float, user_agent: str) -> List[Dict[str, str]]:
    """
    基于 DuckDuckGo HTML/Lite endpoint 的搜索实现（标准库，无额外依赖）。
    """
    query_text = _validate_query(query)
    encoded_query = quote_plus(query_text)
    base_urls = [
        f"https://html.duckduckgo.com/html/?q={encoded_query}",
        f"https://lite.duckduckgo.com/lite/?q={encoded_query}",
    ]
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    last_exc: Exception | None = None
    for idx, url in enumerate(base_urls, start=1):
        logger.info(
            "DuckDuckGo 搜索: url=%s attempt=%d/%d max_results=%d timeout_s=%s",
            url,
            idx,
            len(base_urls),
            max_results,
            timeout_s,
        )
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - 受控访问
                raw = resp.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_exc = exc
            logger.warning("DuckDuckGo 搜索失败: url=%s error=%s", url, exc)
            continue

        html = raw.decode("utf-8", errors="replace")
        parser = _DuckDuckGoHTMLParser(max_results=max_results)
        parser.feed(html)
        results = parser.results
        logger.info(
            "DuckDuckGo 搜索完成: url=%s query_len=%d results=%d",
            url,
            len(query_text),
            len(results),
        )
        return results

    if last_exc is None:
        last_exc = RuntimeError("DuckDuckGo 搜索失败: 未获取到响应")
    raise RuntimeError(f"DuckDuckGo 搜索失败: {last_exc}") from last_exc


def _bing_search_rss(query: str, *, max_results: int, timeout_s: float, user_agent: str) -> List[Dict[str, str]]:
    """
    基于 Bing RSS 的搜索实现，结构比 HTML 页面更稳定。
    """
    query_text = _validate_query(query)
    url = f"https://www.bing.com/search?format=rss&q={quote_plus(query_text)}"
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    logger.info("Bing RSS 搜索: url=%s max_results=%d timeout_s=%s", url, max_results, timeout_s)
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - 受控访问
            raw = resp.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        logger.warning("Bing RSS 搜索失败: url=%s error=%s", url, exc)
        raise RuntimeError(f"Bing RSS 搜索失败: {exc}") from exc

    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        raise RuntimeError(f"Bing RSS 响应解析失败: {exc}") from exc

    results: List[Dict[str, str]] = []
    for item in root.findall(".//item"):
        result = _build_result(
            item.findtext("title", default=""),
            item.findtext("link", default=""),
            item.findtext("description", default=""),
        )
        if result["title"] and result["url"]:
            results.append(result)
        if len(results) >= max_results:
            break

    logger.info("Bing RSS 搜索完成: query_len=%d results=%d", len(query_text), len(results))
    return results


def _serpapi_search_api(
    query: str,
    *,
    search_engine: str,
    max_results: int,
    timeout_s: float,
    user_agent: str,
    api_key: str,
    base_url: str = "https://serpapi.com/search.json",
) -> List[Dict[str, str]]:
    """
    基于 SerpAPI 的 Web Search API（返回 JSON，需 api_key）。

    文档（搜索 API 概览）：
    - https://serpapi.com/search-api
    """
    query_text = _validate_query(query)
    if not isinstance(search_engine, str) or not search_engine.strip():
        raise ValueError("search_engine 必须为非空字符串（例如 google/bing/baidu/google_light_fast 等）")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("SerpAPI api_key 不能为空")
    engine_text = search_engine.strip()
    mr = max(1, int(max_results))

    params = {
        "engine": engine_text,
        "q": query_text,
        "api_key": api_key.strip(),
        "num": str(mr),
        # 尽量压缩返回字段，降低响应体大小（对不支持的 engine 将被忽略）
        "json_restrictor": "organic_results.title,organic_results.link,organic_results.snippet",
    }
    url = f"{base_url}?{urlencode(params, quote_via=quote_plus)}"
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    logger.info(
        "SerpAPI 搜索: url=%s search_engine=%s max_results=%d timeout_s=%s",
        base_url,
        engine_text,
        mr,
        timeout_s,
    )

    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - 受控访问
            raw = resp.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        logger.warning("SerpAPI 搜索失败: search_engine=%s error=%s", engine_text, exc)
        raise RuntimeError(f"SerpAPI 搜索失败: {exc}") from exc

    try:
        payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except Exception as exc:
        raise RuntimeError(f"SerpAPI 响应解析失败: {exc}") from exc

    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"SerpAPI 返回错误: {payload.get('error')}")

    organic = payload.get("organic_results", []) if isinstance(payload, dict) else []
    if not isinstance(organic, list):
        organic = []

    results = _collect_results(
        organic,
        title_keys=("title",),
        url_keys=("link", "url"),
        snippet_keys=("snippet",),
        max_results=mr,
    )

    logger.info(
        "SerpAPI 搜索完成: search_engine=%s query_len=%d results=%d",
        engine_text,
        len(query_text),
        len(results),
    )
    return results


def _tavily_search_api(
    query: str,
    *,
    max_results: int,
    timeout_s: float,
    user_agent: str,
    api_key: str,
    base_url: str = "https://api.tavily.com/search",
) -> List[Dict[str, str]]:
    """
    基于 Tavily Search API（POST /search，需 api_key）。

    文档：
    - https://docs.tavily.com/documentation/api-reference/endpoint/search
    """
    query_text = _validate_query(query)
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("Tavily api_key 不能为空")
    mr = max(1, int(max_results))

    payload = {
        "query": query_text,
        "max_results": mr,
    }
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key.strip()}",
    }

    logger.info(
        "Tavily 搜索: url=%s max_results=%d timeout_s=%s",
        base_url,
        mr,
        timeout_s,
    )
    try:
        req = Request(base_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - 受控访问
            raw = resp.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        logger.warning("Tavily 搜索失败: error=%s", exc)
        raise RuntimeError(f"Tavily 搜索失败: {exc}") from exc

    try:
        response = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except Exception as exc:
        raise RuntimeError(f"Tavily 响应解析失败: {exc}") from exc

    if isinstance(response, dict) and response.get("error"):
        raise RuntimeError(f"Tavily 返回错误: {response.get('error')}")

    items = response.get("results", []) if isinstance(response, dict) else []
    if not isinstance(items, list):
        items = []

    results = _collect_results(
        items,
        title_keys=("title",),
        url_keys=("url", "link"),
        snippet_keys=("content", "snippet"),
        max_results=mr,
    )

    logger.info(
        "Tavily 搜索完成: query_len=%d results=%d",
        len(query_text),
        len(results),
    )
    return results


class WebSearchTool(Tool):
    _execution_mode = "parallel_safe"
    """
    网页搜索工具。

    说明：
    - engine 在初始化时配置（当前内置 duckduckgo / ddg / bing）。
    - max_results 控制返回数量上限（会被 cap 限制以避免过大请求/解析）。
    """

    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "web_search"
    is_read_only = True
    description = "使用网页搜索引擎进行搜索，返回标题/链接/摘要"
    inputs = {
        "query": {"type": "string", "description": "搜索关键词/问题"},
        "max_results": {
            "type": "integer",
            "description": "最大返回结果数，默认 5（会被 cap 限制）",
            "required": False,
        },
    }
    outputs = {
        "results": {"type": "list", "description": "搜索结果列表，每项包含 title/url/snippet"},
        "query": {"type": "string", "description": "原始 query"},
    }

    DEFAULT_MAX_RESULTS = 5
    DEFAULT_TIMEOUT_S = 15.0
    DEFAULT_MAX_RESULTS_CAP = 20
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        *,
        engine: str = "duckduckgo",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        user_agent: str = DEFAULT_USER_AGENT,
        max_results_cap: int = DEFAULT_MAX_RESULTS_CAP,
    ) -> None:
        super().__init__()
        self.timeout_s = float(timeout_s)
        self.user_agent = str(user_agent or self.DEFAULT_USER_AGENT)
        self.max_results_cap = int(max_results_cap)

        self._engine_funcs: Dict[str, Callable[..., List[Dict[str, str]]]] = {
            "duckduckgo": lambda q, max_results: _duckduckgo_search_html(
                q,
                max_results=max_results,
                timeout_s=self.timeout_s,
                user_agent=self.user_agent,
            ),
            "ddg": lambda q, max_results: _duckduckgo_search_html(
                q,
                max_results=max_results,
                timeout_s=self.timeout_s,
                user_agent=self.user_agent,
            ),
            "bing": lambda q, max_results: _bing_search_rss(
                q,
                max_results=max_results,
                timeout_s=self.timeout_s,
                user_agent=self.user_agent,
            ),
        }

        self.engine = engine.strip().lower()
        if self.engine not in self._engine_funcs:
            raise ValueError(f"不支持的 engine: {self.engine}，可选值: {sorted(self._engine_funcs.keys())}")

        logger.info(
            "WebSearchTool 初始化完成 engine=%s timeout_s=%s cap=%s",
            self.engine,
            self.timeout_s,
            self.max_results_cap,
        )

    def forward(
        self,
        query: str,
        max_results: int | None = None,
    ) -> Dict[str, Any]:
        q = _validate_query(query)
        mr = _resolve_max_results(
            max_results=max_results,
            default_value=self.DEFAULT_MAX_RESULTS,
            cap_value=self.max_results_cap,
            logger_name="WebSearchTool",
        )

        logger.info("WebSearchTool 执行: engine=%s query_len=%d max_results=%d", self.engine, len(q), mr)
        results = self._engine_funcs[self.engine](q, max_results=mr)
        return {"query": query, "results": results}


class ApiWebSearchTool(Tool):
    _execution_mode = "parallel_safe"
    """
    需要 API key 的网页搜索工具（当前支持 Tavily / SerpAPI）。

    与 `web_search` 的差异：
    - `engine` 在调用时指定，用于选择 API 提供商（tavily/serpapi）；
    - SerpAPI 的具体搜索引擎通过配置文件指定（google/bing/baidu/...）。
    """

    max_observation_chars = LIST_OBSERVATION_CHARS
    name = "api_web_search"
    is_read_only = True
    description = "使用需要 API key 的搜索 API（Tavily/SerpAPI）进行搜索，返回标题/链接/摘要"
    inputs = {
        "query": {"type": "string", "description": "搜索关键词/问题"},
        "max_results": {
            "type": "integer",
            "description": "最大返回结果数，默认 5（会被 cap 限制）",
            "required": False,
        },
    }
    outputs = {
        "results": {"type": "list", "description": "搜索结果列表，每项包含 title/url/snippet"},
        "query": {"type": "string", "description": "原始 query"},
    }

    DEFAULT_ENGINE = "tavily"
    DEFAULT_SERPAPI_SEARCH_ENGINE = "google"
    SUPPORTED_ENGINES = {"tavily", "serpapi"}
    DEFAULT_ENGINE_SECTIONS = {
        "tavily": "tools.Tavily",
        "serpapi": "tools.SerpAPI",
    }
    DEFAULT_ENGINE_BASE_URLS = {
        "tavily": "https://api.tavily.com/search",
        "serpapi": "https://serpapi.com/search.json",
    }
    DEFAULT_MAX_RESULTS = 5
    DEFAULT_TIMEOUT_S = 15.0
    DEFAULT_MAX_RESULTS_CAP = 20
    DEFAULT_USER_AGENT = WebSearchTool.DEFAULT_USER_AGENT
    # 默认从 SDK 只读资源加载配置，并允许 workspace 配置覆盖。
    DEFAULT_CONFIG_PATH = str(DEFAULT_RUNTIME_CONFIG_PATH)

    def __init__(
        self,
        *,
        engine: str = DEFAULT_ENGINE,
        api_key: str | None = None,
        config_path: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        user_agent: str = DEFAULT_USER_AGENT,
        max_results_cap: int = DEFAULT_MAX_RESULTS_CAP,
        engine_sections: Dict[str, str] | None = None,
        engine_base_urls: Dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.engine = str(engine or self.DEFAULT_ENGINE).strip().lower()
        if self.engine not in self.SUPPORTED_ENGINES:
            raise ValueError(f"不支持的 engine: {self.engine}，可选值: {sorted(self.SUPPORTED_ENGINES)}")

        self._api_key = api_key
        self.config_path = str(config_path) if config_path is not None else self.DEFAULT_CONFIG_PATH
        self.timeout_s = float(timeout_s)
        self.user_agent = str(user_agent or self.DEFAULT_USER_AGENT)
        self.max_results_cap = int(max_results_cap)
        self.engine_sections = dict(self.DEFAULT_ENGINE_SECTIONS)
        if engine_sections:
            self.engine_sections.update({str(k).strip().lower(): str(v) for k, v in engine_sections.items()})
        self.engine_base_urls = dict(self.DEFAULT_ENGINE_BASE_URLS)
        if engine_base_urls:
            self.engine_base_urls.update({str(k).strip().lower(): str(v) for k, v in engine_base_urls.items()})

        logger.info(
            "ApiWebSearchTool 初始化完成 engine=%s config=%s timeout_s=%s cap=%s engines_supported=%s",
            self.engine,
            self.config_path,
            self.timeout_s,
            self.max_results_cap,
            sorted(self.engine_sections.keys()),
        )

    def _resolve_api_key(self, engine: str) -> str:
        key = (self._api_key or "").strip()
        if key:
            return key
        tool_config = self._read_tool_config(engine)
        env_name = str(tool_config.get("api_key_env") or "").strip()
        env_values = load_env_file(DEFAULT_DOTENV_PATH)
        env_key = resolve_env_value(env_name, dotenv_values=env_values)
        if env_key:
            return env_key
        raise ValueError(
            f"未配置 {engine} api_key：请在初始化 ApiWebSearchTool(api_key=...) 传入，"
            f"或在配置文件 {self.config_path} 的 tools.{normalize_runtime_key(self._resolve_section_name(engine))} 中"
            f"配置 api_key_env，并在项目根 .env 中写入对应密钥"
        )

    def _resolve_section_name(self, engine: str) -> str:
        """
        解析标准工具配置名称，仅支持 tools.* 命名空间。
        """
        configured = str(self.engine_sections.get(engine, "") or "").strip()
        if not configured:
            return ""
        if configured.startswith("tools."):
            return configured
        return f"tools.{configured}"

    def _read_tool_config(self, engine: str) -> dict[str, Any]:
        tool_name = normalize_runtime_key(self._resolve_section_name(engine))
        if not tool_name:
            return {}
        tools = get_runtime_mapping("tools", config_path=self.config_path)
        raw = tools.get(tool_name)
        return dict(raw) if isinstance(raw, dict) else {}

    def forward(
        self,
        query: str,
        max_results: int | None = None,
    ) -> Dict[str, Any]:
        q = _validate_query(query)
        eng = self.engine
        mr = _resolve_max_results(
            max_results=max_results,
            default_value=self.DEFAULT_MAX_RESULTS,
            cap_value=self.max_results_cap,
            logger_name="ApiWebSearchTool",
        )

        logger.info("ApiWebSearchTool 执行: engine=%s query_len=%d max_results=%d", eng, len(q), mr)
        api_key = self._resolve_api_key(eng)
        tool_config = self._read_tool_config(eng)
        base_url = str(tool_config.get("base_url") or "").strip() or self.engine_base_urls.get(eng, "")

        if eng == "tavily":
            results = _tavily_search_api(
                q,
                max_results=mr,
                timeout_s=self.timeout_s,
                user_agent=self.user_agent,
                api_key=api_key,
                base_url=base_url or self.DEFAULT_ENGINE_BASE_URLS["tavily"],
            )
        else:
            se = str(tool_config.get("search_engine") or "").strip() or self.DEFAULT_SERPAPI_SEARCH_ENGINE
            results = _serpapi_search_api(
                q,
                search_engine=se,
                max_results=mr,
                timeout_s=self.timeout_s,
                user_agent=self.user_agent,
                api_key=api_key,
                base_url=base_url or self.DEFAULT_ENGINE_BASE_URLS["serpapi"],
            )
        return {"query": query, "results": results}


WEB_TOOLS = {
    "web_search": WebSearchTool(),
    # api_key 建议通过统一 config.yaml + .env 配置；未配置时仅在调用 forward 时抛错，不影响导入/初始化
    "api_web_search": ApiWebSearchTool(),
}


__all__ = [
    "ApiWebSearchTool",
    "WEB_TOOLS",
    "WebSearchTool",
]
