"""Shared Playwright session for the built-in web browser tools.

The session object owns all direct Playwright calls. Tool classes stay thin so
policy checks, runner binding, and prompt-facing schemas do not leak Playwright
details throughout the codebase.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future
from datetime import datetime
import fnmatch
import json
import logging
import platform
import queue
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

DEFAULT_BROWSER_SCREENSHOTS_DIR = ".juice/browser_screenshots"
DEFAULT_BROWSER_STATE_DIR = ".juice/browser_state"
DEFAULT_BROWSER_RECORDINGS_DIR = ".juice/browser_recordings"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
SUPPORTED_SEARCH_ENGINES = {"duckduckgo", "ddg", "bing", "baidu"}
SECURITY_VERIFICATION_MESSAGE = "页面进入安全验证或验证码流程；浏览器工具不会尝试绕过验证。"
EDITABLE_CANDIDATE_SELECTOR = (
    "input:not([type='hidden']):not([disabled]), "
    "textarea:not([disabled]), "
    "[contenteditable='true'], "
    "[role='textbox']"
)


def _parse_version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for item in (version or "").split("."):
        if not item.isdigit():
            break
        parts.append(int(item))
    return tuple(parts)


def _format_playwright_start_error(exc: Exception, *, libc_info: tuple[str, str] | None = None) -> str:
    libc_name, libc_ver = libc_info or platform.libc_ver()
    libc_name = (libc_name or "").strip().lower()
    libc_ver = (libc_ver or "").strip()
    original_msg = str(exc) or exc.__class__.__name__
    hints: list[str] = [f"原始错误: {repr(exc)}"]
    exc_text = original_msg.lower()
    if "asyncio loop" in exc_text or "sync api inside the asyncio" in exc_text:
        hints.append("检测到 sync Playwright 在 asyncio 事件循环线程中启动。")
        hints.append("请确认 gateway 已重启到最新版；浏览器 session 创建和操作必须通过 owner 线程队列执行。")
    elif "glibc" in exc_text or "libc.so" in exc_text:
        if libc_name == "glibc" and libc_ver:
            current = _parse_version_tuple(libc_ver)
            required = _parse_version_tuple("2.27")
            if current and required and current < required:
                hints.append(f"检测到系统 glibc={libc_ver}，低于 Playwright driver 所需版本（通常 >= 2.27）。")
                hints.append("可选方案：升级系统 glibc/OS，或在兼容环境（容器/新镜像）中运行浏览器工具。")
    else:
        hints.append("请确认已安装并初始化 Playwright: pip install playwright && playwright install chromium")
    if "PlaywrightContextManager" in original_msg and "_playwright" in original_msg:
        hints.append("该 AttributeError 通常是底层 driver 进程启动失败后的连带错误。")
    return "Playwright 启动失败。\n" + "\n".join(f"- {hint}" for hint in hints)


def _as_clean_text(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _truncate_text(value: Any, max_len: int = 240) -> str:
    text = _as_clean_text(str(value or ""))
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _validate_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必须为非空字符串")
    return query.strip()


def _build_result(title: Any, url: Any, snippet: Any) -> dict[str, str]:
    return {
        "title": _as_clean_text(str(title or "")),
        "url": _as_clean_text(str(url or "")),
        "snippet": _as_clean_text(str(snippet or "")),
    }


def _css_attr_selector(tag: str, attr: str, value: str) -> str:
    safe = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'{tag}[{attr}="{safe}"]'


def _json_safe(value: Any, *, max_chars: int) -> tuple[Any, bool]:
    """Return a JSON-friendly value and whether it had to be truncated."""

    try:
        raw = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        raw = json.dumps(str(value), ensure_ascii=False)
    truncated = len(raw) > max_chars
    if truncated:
        raw = raw[: max_chars - 3] + "..."
    try:
        return json.loads(raw), truncated
    except Exception:
        return raw, truncated


class PlaywrightBrowserSession:
    """Lazy, multi-tab Playwright Chromium session."""

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_ms: int = 15000,
        user_agent: str = DEFAULT_USER_AGENT,
        viewport_width: int = 1280,
        viewport_height: int = 900,
        state_dir: str | Path = DEFAULT_BROWSER_STATE_DIR,
        recording_dir: str | Path = DEFAULT_BROWSER_RECORDINGS_DIR,
    ) -> None:
        self.headless = bool(headless)
        self.timeout_ms = int(timeout_ms)
        self.user_agent = str(user_agent or DEFAULT_USER_AGENT)
        self.viewport_width = int(viewport_width)
        self.viewport_height = int(viewport_height)
        self.state_dir = Path(state_dir)
        self.recording_dir = Path(recording_dir)
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._tabs: dict[str, Any] = {}
        self._active_tab_id = ""
        self._tab_counter = 0
        self._console_logs: dict[str, deque[dict[str, Any]]] = {}
        self._page_errors: dict[str, deque[dict[str, Any]]] = {}
        self._recording = False
        self._recording_id = ""
        self._recording_path: Path | None = None
        self._command_queue: queue.Queue[tuple[Callable[[], Any], Future[Any]] | None] = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._worker_ready = threading.Event()
        self._owner_thread_id: int | None = None
        self._screencasts: dict[str, Any] = {}
        self._screencast_counter = 0

    def _ensure_browser_thread(self) -> None:
        """Start the single thread that owns Playwright sync objects.

        Playwright's sync API is not safe to call from arbitrary threads once a
        browser is created. All public session methods are therefore marshalled
        through this queue unless they are already running on the owner thread.
        """

        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._worker_ready.clear()
        self._worker_thread = threading.Thread(
            target=self._browser_thread_main,
            name="juice-playwright-session",
            daemon=True,
        )
        self._worker_thread.start()
        self._worker_ready.wait(timeout=5)

    def _browser_thread_main(self) -> None:
        self._owner_thread_id = threading.get_ident()
        self._worker_ready.set()
        while True:
            try:
                item = self._command_queue.get(timeout=0.02)
            except queue.Empty:
                self._pump_browser_events()
                continue
            if item is None:
                return
            callback, future = item
            try:
                future.set_result(callback())
            except Exception as exc:
                future.set_exception(exc)
            finally:
                self._pump_browser_events()

    def _pump_browser_events(self) -> None:
        if self._context is None or not self._active_tab_id:
            return
        page = self._tabs.get(self._active_tab_id)
        if page is None:
            return
        try:
            # CDP screencast frames are delivered while the sync dispatcher is
            # pumped. A tiny timeout keeps live preview moving when pages are
            # otherwise idle.
            page.wait_for_timeout(1)
        except Exception as exc:
            if self._is_playwright_thread_error(exc):
                logger.debug("browser event pump skipped after Playwright thread mismatch", exc_info=True)
                return
            logger.debug("browser event pump failed", exc_info=True)

    def run_on_browser_thread(self, callback: Callable[[], Any]) -> Any:
        """Run ``callback`` on the Playwright owner thread and return its result."""

        if threading.get_ident() == self._owner_thread_id:
            return callback()
        self._ensure_browser_thread()
        future: Future[Any] = Future()
        self._command_queue.put((callback, future))
        return future.result()

    def start_screencast(self, on_frame: Callable[[dict[str, Any]], None]) -> str:
        """Start a CDP screencast for the active page.

        The callback receives JSON-serializable frame metadata and is invoked on
        the browser owner thread, so it must hand work off quickly.
        """

        return str(self.run_on_browser_thread(lambda: self._start_screencast(on_frame)))

    def _start_screencast(self, on_frame: Callable[[dict[str, Any]], None]) -> str:
        try:
            return self._start_screencast_once(on_frame)
        except Exception as exc:
            if not self._is_playwright_thread_error(exc):
                raise
            previous_url = self._best_effort_active_url()
            logger.warning("Playwright screencast thread mismatch; rebuilding browser session")
            self._reset_playwright_runtime(reason="screencast thread mismatch")
            self._ensure_started()
            if previous_url and previous_url != "about:blank":
                try:
                    self.open_url(previous_url)
                except Exception:
                    logger.debug("failed to restore browser url after thread recovery: %s", previous_url, exc_info=True)
            return self._start_screencast_once(on_frame)

    def _start_screencast_once(self, on_frame: Callable[[dict[str, Any]], None]) -> str:
        if not self._active_tab_id or self._active_tab_id not in self._tabs:
            return ""
        self._ensure_started()
        page = self.page()
        cdp = self._context.new_cdp_session(page)
        cdp.send("Page.enable")
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 72, "everyNthFrame": 1})
        self._screencast_counter += 1
        token = f"screencast_{self._screencast_counter}"

        def handle_frame(payload: dict[str, Any]) -> None:
            metadata = payload.get("metadata") or {}
            try:
                on_frame({
                    "type": "frame",
                    "data": payload.get("data", ""),
                    "url": self.current_url(),
                    "title": _as_clean_text(page.title() or ""),
                    "width": int(metadata.get("deviceWidth") or self.viewport_width),
                    "height": int(metadata.get("deviceHeight") or self.viewport_height),
                })
            finally:
                try:
                    cdp.send("Page.screencastFrameAck", {"sessionId": payload.get("sessionId")})
                except Exception:
                    logger.debug("failed to ack screencast frame", exc_info=True)

        cdp.on("Page.screencastFrame", handle_frame)
        self._screencasts[token] = cdp
        return token

    def _is_playwright_thread_error(self, exc: Exception | None = None) -> bool:
        message = "" if exc is None else str(exc)
        if not message:
            return False
        return "Cannot switch to a different thread" in message or "greenlet" in type(exc).__module__.lower()

    def _best_effort_active_url(self) -> str:
        try:
            if self._active_tab_id and self._active_tab_id in self._tabs:
                return str(getattr(self._tabs[self._active_tab_id], "url", "") or "")
        except Exception:
            return ""
        return ""

    def _reset_playwright_runtime(self, *, reason: str) -> None:
        logger.info("Resetting Playwright browser runtime: %s", reason)
        for cdp in list(self._screencasts.values()):
            try:
                cdp.send("Page.stopScreencast")
            except Exception:
                pass
        self._screencasts.clear()
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._tabs = {}
        self._active_tab_id = ""

    def stop_screencast(self, token: str) -> None:
        self.run_on_browser_thread(lambda: self._stop_screencast(token))

    def _stop_screencast(self, token: str) -> None:
        cdp = self._screencasts.pop(str(token), None)
        if cdp is None:
            return
        try:
            cdp.send("Page.stopScreencast")
        except Exception:
            logger.debug("failed to stop screencast", exc_info=True)

    def ensure_visible_window(self) -> dict[str, Any]:
        """Ensure this session is backed by a visible Chromium window."""

        return dict(self.run_on_browser_thread(self._ensure_visible_window))

    def _ensure_visible_window(self) -> dict[str, Any]:
        previous_url = ""
        if self._context is not None and self._active_tab_id:
            try:
                previous_url = self.current_url()
            except Exception:
                previous_url = ""
        if self.headless and self._context is not None:
            for cdp in list(self._screencasts.values()):
                try:
                    cdp.send("Page.stopScreencast")
                except Exception:
                    pass
            self._screencasts.clear()
            try:
                self._context.close()
            except Exception:
                pass
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
            self._context = None
            self._tabs = {}
            self._active_tab_id = ""
        self.headless = False
        self._ensure_started()
        if previous_url and previous_url != "about:blank":
            try:
                self.open_url(previous_url)
            except Exception:
                logger.debug("failed to restore visible browser url=%s", previous_url, exc_info=True)
        page = self.page()
        try:
            page.bring_to_front()
        except Exception:
            logger.debug("failed to focus visible browser", exc_info=True)
        return {"status": "visible", "headless": False, "url": self.current_url(), "tabs": self.list_tabs()["tabs"]}

    def _ensure_started(self) -> None:
        if self._context is not None and self._tabs:
            return
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[reportMissingImports]
        except Exception as exc:
            raise ImportError(
                "未安装 playwright。请先执行: pip install playwright && playwright install chromium"
            ) from exc
        try:
            if self._pw is None:
                self._pw = sync_playwright().start()
            if self._browser is None:
                self._browser = self._pw.chromium.launch(
                    headless=self.headless,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
            self._new_context()
            self._register_page(self._context.new_page(), make_active=True)
            logger.info(
                "Playwright browser session started headless=%s timeout_ms=%s viewport=%sx%s",
                self.headless,
                self.timeout_ms,
                self.viewport_width,
                self.viewport_height,
            )
        except Exception as exc:
            logger.exception("Playwright 会话启动失败: %s", exc)
            self._pw = None
            self._browser = None
            self._context = None
            self._tabs = {}
            self._active_tab_id = ""
            raise RuntimeError(_format_playwright_start_error(exc)) from exc

    def _new_context(self, *, storage_state: str | Path | None = None, record_video_dir: Path | None = None) -> None:
        kwargs: dict[str, Any] = {
            "user_agent": self.user_agent,
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
        }
        if storage_state is not None:
            kwargs["storage_state"] = str(storage_state)
        if record_video_dir is not None:
            kwargs["record_video_dir"] = str(record_video_dir)
            kwargs["record_video_size"] = {"width": self.viewport_width, "height": self.viewport_height}
        self._context = self._browser.new_context(**kwargs)
        try:
            self._context.on("page", lambda page: self._register_page(page, make_active=True))
        except Exception:
            logger.debug("Browser context does not support page event registration", exc_info=True)
        self._tabs = {}
        self._active_tab_id = ""

    def _register_page(self, page: Any, *, make_active: bool = True) -> str:
        for existing_tab_id, existing_page in self._tabs.items():
            if existing_page is page:
                self._console_logs.setdefault(existing_tab_id, deque(maxlen=500))
                self._page_errors.setdefault(existing_tab_id, deque(maxlen=500))
                if make_active or not self._active_tab_id:
                    self._active_tab_id = existing_tab_id
                return existing_tab_id

        self._tab_counter += 1
        tab_id = f"tab_{self._tab_counter}"
        self._tabs[tab_id] = page
        self._console_logs[tab_id] = deque(maxlen=500)
        self._page_errors[tab_id] = deque(maxlen=500)
        try:
            page.set_default_timeout(self.timeout_ms)
        except Exception:
            pass
        try:
            page.on("console", lambda msg, tid=tab_id: self._record_console(tid, msg))
            page.on("pageerror", lambda err, tid=tab_id: self._record_page_error(tid, err))
        except Exception:
            logger.debug("Page does not support console/pageerror events", exc_info=True)
        if make_active or not self._active_tab_id:
            self._active_tab_id = tab_id
        return tab_id

    def _record_console(self, tab_id: str, msg: Any) -> None:
        location = {}
        try:
            location = dict(msg.location or {})
        except Exception:
            location = {}
        self._console_logs.setdefault(tab_id, deque(maxlen=500)).append(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "type": str(getattr(msg, "type", "") or ""),
                "text": str(getattr(msg, "text", "") or ""),
                "location": location,
                "tab_id": tab_id,
            }
        )

    def _record_page_error(self, tab_id: str, err: Any) -> None:
        self._page_errors.setdefault(tab_id, deque(maxlen=500)).append(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "type": err.__class__.__name__,
                "text": str(err),
                "tab_id": tab_id,
            }
        )

    def reconfigure(
        self,
        *,
        headless: bool | None = None,
        timeout_ms: int | None = None,
        user_agent: str | None = None,
        viewport_width: int | None = None,
        viewport_height: int | None = None,
        state_dir: str | Path | None = None,
        recording_dir: str | Path | None = None,
    ) -> None:
        if headless is not None and self._context is None:
            self.headless = bool(headless)
        if timeout_ms is not None:
            self.timeout_ms = int(timeout_ms)
            for page in self._tabs.values():
                try:
                    page.set_default_timeout(self.timeout_ms)
                except Exception:
                    pass
        if user_agent is not None and self._context is None:
            self.user_agent = str(user_agent or DEFAULT_USER_AGENT)
        if viewport_width is not None and self._context is None:
            self.viewport_width = int(viewport_width)
        if viewport_height is not None and self._context is None:
            self.viewport_height = int(viewport_height)
        if state_dir is not None:
            self.state_dir = Path(state_dir)
        if recording_dir is not None:
            self.recording_dir = Path(recording_dir)

    def _resolve_tab(self, tab_id: str | None = None) -> tuple[str, Any]:
        if not self._tabs and self._page is not None:
            return self._register_page(self._page, make_active=True), self._page
        self._ensure_started()
        resolved = str(tab_id or self._active_tab_id or "").strip()
        if not resolved or resolved not in self._tabs:
            raise ValueError(f"未知浏览器 tab_id: {tab_id}")
        return resolved, self._tabs[resolved]

    def page(self, tab_id: str | None = None) -> Any:
        return self._resolve_tab(tab_id)[1]

    def current_url(self, tab_id: str | None = None) -> str:
        _, p = self._resolve_tab(tab_id)
        return str(getattr(p, "url", "") or "")

    def tab_snapshot(self) -> dict[str, Any]:
        """Return known tab metadata without starting Playwright or creating pages."""

        tabs = []
        for tab_id, page in self._tabs.items():
            tabs.append(
                {
                    "tab_id": tab_id,
                    "url": str(getattr(page, "url", "") or ""),
                    "title": _as_clean_text(page.title() or ""),
                    "active": tab_id == self._active_tab_id,
                }
            )
        return {"tabs": tabs, "active_tab_id": self._active_tab_id}

    def status(self) -> dict[str, Any]:
        snapshot = self.tab_snapshot()
        return {
            "backend": "in_app",
            "active_tab_id": snapshot["active_tab_id"],
            "tabs": snapshot["tabs"],
            "recording": self._recording,
        }

    def open_url(self, url: str, wait_until: str = "domcontentloaded", tab_id: str | None = None) -> dict[str, Any]:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url 必须为非空字符串")
        u = url.strip()
        if not re.match(r"^https?://", u, re.IGNORECASE):
            u = "https://" + u
        resolved_tab_id, p = self._resolve_tab(tab_id)
        attempts = [(str(wait_until), int(self.timeout_ms)), ("load", max(int(self.timeout_ms) * 2, 30000))]
        last_exc: Exception | None = None
        for attempt_wait_until, attempt_timeout in attempts:
            try:
                p.goto(u, wait_until=attempt_wait_until, timeout=attempt_timeout)
                break
            except Exception as exc:
                last_exc = exc
                message = str(exc)
                if "Timeout" not in message and "timed out" not in message.lower():
                    raise
                logger.warning("browser open_url timeout retry url=%s wait_until=%s", u, attempt_wait_until)
        if last_exc is not None and str(getattr(p, "url", "") or "") in {"", "about:blank"}:
            raise last_exc
        return {"tab_id": resolved_tab_id, "url": str(getattr(p, "url", "") or ""), "title": _as_clean_text(p.title() or "")}

    def go_back(self, wait_until: str = "domcontentloaded", tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, p = self._resolve_tab(tab_id)
        p.go_back(wait_until=wait_until)
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "title": _as_clean_text(p.title() or "")}

    def close_popups(self, tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, p = self._resolve_tab(tab_id)
        p.keyboard.press("Escape")
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "status": "ok"}

    def find_text(self, text: str, nth_result: int = 1, tab_id: str | None = None) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text 必须为非空字符串")
        n = int(nth_result)
        if n <= 0:
            raise ValueError("nth_result 必须为正整数")
        resolved_tab_id, p = self._resolve_tab(tab_id)
        locator = p.locator(f"text={text.strip()}")
        count = locator.count()
        if count <= 0:
            raise ValueError(f"未找到文本: {text}")
        if n > count:
            raise ValueError(f"nth_result={n} 超过匹配数量 {count}")
        target = locator.nth(n - 1)
        target.scroll_into_view_if_needed()
        preview = _truncate_text(target.inner_text() or "")
        return {"tab_id": resolved_tab_id, "matches": count, "focused_index": n, "url": self.current_url(resolved_tab_id), "preview": preview}

    def screenshot(self, *, save_path: str | None = None, full_page: bool = True, tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, p = self._resolve_tab(tab_id)
        out_path = Path(save_path).expanduser() if save_path else Path(DEFAULT_BROWSER_SCREENSHOTS_DIR) / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        p.screenshot(path=str(out_path), full_page=bool(full_page))
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "path": str(out_path), "full_page": bool(full_page)}

    def search_detailed(self, query: str, *, engine: str, max_results: int, tab_id: str | None = None) -> dict[str, Any]:
        q = _validate_query(query)
        eng = (engine or "duckduckgo").strip().lower()
        if eng not in SUPPORTED_SEARCH_ENGINES:
            raise ValueError(f"不支持的 engine: {eng}，可选值: {sorted(SUPPORTED_SEARCH_ENGINES)}")
        _, p = self._resolve_tab(tab_id)
        status = "ok"
        message = ""
        if eng in {"duckduckgo", "ddg"}:
            p.goto(f"https://duckduckgo.com/?q={quote_plus(q)}", wait_until="domcontentloaded")
            results = self._extract_ddg_results(p, max_results=max_results)
        elif eng == "bing":
            p.goto(f"https://www.bing.com/search?q={quote_plus(q)}", wait_until="domcontentloaded")
            results = self._extract_bing_results(p, max_results=max_results)
        else:
            # 百度结果页在自动化环境中可能直接进入验证码；这里只识别并返回受阻状态，不尝试绕过。
            p.goto(f"https://www.baidu.com/s?wd={quote_plus(q)}", wait_until="domcontentloaded")
            if self._is_security_verification_page(p):
                return {
                    "query": query,
                    "engine": "baidu",
                    "status": "blocked",
                    "message": SECURITY_VERIFICATION_MESSAGE,
                    "results": [],
                }
            results = self._extract_baidu_results(p, max_results=max_results)
        if results:
            return {"query": query, "engine": eng, "status": status, "message": message, "results": results}
        if eng == "baidu":
            return {
                "query": query,
                "engine": "baidu",
                "status": status,
                "message": "百度搜索页未解析到可用结果。",
                "results": [],
            }

        # DuckDuckGo may return a protection/418 page to automated Chromium
        # while its lightweight HTTP endpoint still returns valid data.  A
        # data-only fallback makes the tool result look successful but leaves
        # Juice Web's live canvas on an unrelated protection page.  Try real
        # search pages in the same tab first.  Bing remains the general-purpose
        # first fallback; Baidu is the second because automated Bing can also
        # show a challenge page while Baidu still serves visible results.
        if eng in {"duckduckgo", "ddg"}:
            logger.info(
                "browser_search DuckDuckGo 页面无可用结果，尝试同 tab 可见搜索降级: query_len=%d",
                len(q),
            )
            visible_fallbacks = (
                ("bing", f"https://www.bing.com/search?q={quote_plus(q)}", self._extract_bing_results),
                ("baidu", f"https://www.baidu.com/s?wd={quote_plus(q)}", self._extract_baidu_results),
            )
            for fallback_engine, fallback_url, extract_results in visible_fallbacks:
                try:
                    p.goto(fallback_url, wait_until="domcontentloaded")
                    if fallback_engine == "baidu" and self._is_security_verification_page(p):
                        logger.info("browser_search 可见 Baidu 降级进入安全验证，继续数据兜底")
                        continue
                    visible_fallback_results = extract_results(p, max_results=max_results)
                except Exception as exc:
                    logger.warning("browser_search 可见 %s 降级失败: %s", fallback_engine, exc)
                    continue
                if visible_fallback_results:
                    return {
                        "query": query,
                        "requested_engine": eng,
                        "engine": fallback_engine,
                        "status": "fallback",
                        "message": f"DuckDuckGo 页面无可用结果，已在当前浏览器 tab 使用 {fallback_engine} 展示搜索结果。",
                        "results": visible_fallback_results,
                    }

        logger.warning("browser_search 页面解析为空，触发兜底搜索: engine=%s query_len=%d", eng, len(q))
        from juice_agents.core.agent.tools.builtin.web.web_search_tools import _bing_search_rss, _duckduckgo_search_html
        if eng in {"duckduckgo", "ddg"}:
            fallback_results = _duckduckgo_search_html(q, max_results=max_results, timeout_s=max(5.0, self.timeout_ms / 1000), user_agent=self.user_agent)
        else:
            fallback_results = _bing_search_rss(q, max_results=max_results, timeout_s=max(5.0, self.timeout_ms / 1000), user_agent=self.user_agent)
        return {"query": query, "engine": eng, "status": status, "message": message, "results": fallback_results}

    def search(self, query: str, *, engine: str, max_results: int, tab_id: str | None = None) -> list[dict[str, str]]:
        return self.search_detailed(query, engine=engine, max_results=max_results, tab_id=tab_id)["results"]

    def _is_security_verification_page(self, page: Any) -> bool:
        url = str(getattr(page, "url", "") or "").lower()
        if "wappass.baidu.com" in url or "captcha" in url:
            return True
        try:
            title = _as_clean_text(page.title() or "")
        except Exception:
            title = ""
        if "安全验证" in title or "验证码" in title:
            return True
        try:
            body = page.locator("body").inner_text(timeout=1000)
        except Exception:
            body = ""
        return "安全验证" in body or "验证码" in body

    def _extract_search_results(self, items: list[Any], *, title_selector: str, link_selector: str, snippet_selectors: tuple[str, ...], max_results: int) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for item in items:
            title_el = item.query_selector(title_selector)
            link_el = item.query_selector(link_selector)
            snippet_el = None
            for selector in snippet_selectors:
                snippet_el = item.query_selector(selector)
                if snippet_el is not None:
                    break
            result = _build_result(
                title_el.inner_text() if title_el else "",
                link_el.get_attribute("href") if link_el else "",
                snippet_el.inner_text() if snippet_el else "",
            )
            if result["title"] and result["url"]:
                out.append(result)
            if len(out) >= max_results:
                break
        return out

    def _extract_ddg_results(self, page: Any, *, max_results: int) -> list[dict[str, str]]:
        return self._extract_search_results(
            page.query_selector_all("article[data-testid='result']") or [],
            title_selector="h2",
            link_selector="h2 a",
            snippet_selectors=("[data-result='snippet']", "[data-testid='result-snippet']", "div"),
            max_results=max_results,
        )

    def _extract_bing_results(self, page: Any, *, max_results: int) -> list[dict[str, str]]:
        return self._extract_search_results(
            page.query_selector_all("li.b_algo") or [],
            title_selector="h2",
            link_selector="h2 a",
            snippet_selectors=("div.b_caption p", "p.b_lineclamp2"),
            max_results=max_results,
        )

    def _extract_baidu_results(self, page: Any, *, max_results: int) -> list[dict[str, str]]:
        return self._extract_search_results(
            page.query_selector_all("div.result, div.c-container") or [],
            title_selector="h3",
            link_selector="h3 a, a",
            snippet_selectors=(".c-abstract", ".content-right_8Zs40", ".c-color-text", "div"),
            max_results=max_results,
        )

    def _locator(self, selector: str, tab_id: str | None = None) -> tuple[str, Any, Any]:
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("selector 必须为非空字符串")
        resolved_tab_id, p = self._resolve_tab(tab_id)
        return resolved_tab_id, p, p.locator(selector.strip())

    def _locator_state(self, locator: Any) -> dict[str, Any]:
        try:
            count = int(locator.count())
        except Exception:
            count = 0
        if count <= 0:
            return {"count": 0, "visible": False, "enabled": False, "editable": False, "bounding_box": None}
        first = locator.first
        try:
            visible = bool(first.is_visible())
        except Exception:
            visible = False
        try:
            enabled = bool(first.is_enabled())
        except Exception:
            enabled = False
        try:
            editable = bool(first.is_editable())
        except Exception:
            editable = False
        try:
            bounding_box = first.bounding_box()
        except Exception:
            bounding_box = None
        return {
            "count": count,
            "visible": visible,
            "enabled": enabled,
            "editable": editable,
            "bounding_box": bounding_box,
        }

    def _editable_attrs(self, locator: Any) -> dict[str, str]:
        try:
            attrs = locator.evaluate("(el) => Object.fromEntries(Array.from(el.attributes || []).map(a => [a.name, a.value]))")
        except Exception:
            attrs = {}
        return {str(k): str(v) for k, v in attrs.items()} if isinstance(attrs, dict) else {}

    def _editable_tag(self, locator: Any) -> str:
        try:
            return _as_clean_text(locator.evaluate("(el) => el.tagName.toLowerCase()") or "input") or "input"
        except Exception:
            return "input"

    def _suggest_editable_selector(self, locator: Any, index: int) -> str:
        tag = self._editable_tag(locator)
        attrs = self._editable_attrs(locator)
        for attr in ("id", "name", "placeholder", "aria-label"):
            value = attrs.get(attr)
            if value:
                return _css_attr_selector(tag, attr, value)
        return f"{EDITABLE_CANDIDATE_SELECTOR} >> nth={index}"

    def _visible_editable_candidates(self, page: Any, *, limit: int = 5) -> list[dict[str, Any]]:
        locator = page.locator(EDITABLE_CANDIDATE_SELECTOR)
        try:
            count = int(locator.count())
        except Exception:
            count = 0
        candidates: list[dict[str, Any]] = []
        for idx in range(count):
            item = locator.nth(idx)
            state = self._locator_state(item)
            if not (state["visible"] and state["enabled"] and state["editable"] and state["bounding_box"]):
                continue
            attrs = self._editable_attrs(item)
            candidates.append(
                {
                    "selector": self._suggest_editable_selector(item, idx),
                    "tag": self._editable_tag(item),
                    "placeholder": attrs.get("placeholder", ""),
                    "name": attrs.get("name", ""),
                    "id": attrs.get("id", ""),
                    "bounding_box": state["bounding_box"],
                    "locator": item,
                }
            )
            if len(candidates) >= limit:
                break
        return candidates

    def _public_fill_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: v for k, v in candidate.items() if k != "locator"} for candidate in candidates]

    def _page_url(self, page: Any) -> str:
        return str(getattr(page, "url", "") or "")

    def _read_editable_value(self, locator: Any) -> str | None:
        try:
            return str(locator.input_value(timeout=1000))
        except Exception:
            try:
                return str(locator.inner_text(timeout=1000))
            except Exception:
                return None

    def _keyboard_fill_candidate(self, page: Any, candidate: dict[str, Any], value: str, *, clear_first: bool) -> None:
        locator = candidate["locator"]
        locator.click()
        if clear_first:
            locator.press("Control+A")
            locator.press("Backspace")
        page.keyboard.insert_text(str(value))
        actual = self._read_editable_value(locator)
        if actual is not None and actual != str(value):
            raise RuntimeError(f"键盘输入校验失败: expected={value!r} actual={actual!r}")

    def _fill_failure(
        self,
        *,
        tab_id: str,
        page: Any,
        selector: str,
        reason: str,
        state: dict[str, Any],
        candidates: list[dict[str, Any]],
        message: str = "",
    ) -> dict[str, Any]:
        return {
            "tab_id": tab_id,
            "url": self._page_url(page),
            "selector": selector,
            "status": "failed",
            "reason": reason,
            "message": message,
            "matched_count": state.get("count", 0),
            "visible": state.get("visible", False),
            "enabled": state.get("enabled", False),
            "editable": state.get("editable", False),
            "bounding_box": state.get("bounding_box"),
            "candidates": self._public_fill_candidates(candidates),
        }

    def click(self, selector: str, *, tab_id: str | None = None, button: str = "left", click_count: int = 1, modifiers: list[str] | None = None) -> dict[str, Any]:
        resolved_tab_id, _, locator = self._locator(selector, tab_id)
        locator.click(button=button, click_count=int(click_count), modifiers=list(modifiers or []))
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "selector": selector, "status": "ok"}

    def fill(self, selector: str, value: str, *, tab_id: str | None = None, clear_first: bool = True) -> dict[str, Any]:
        resolved_tab_id, p, locator = self._locator(selector, tab_id)
        if self._is_security_verification_page(p):
            return {
                "tab_id": resolved_tab_id,
                "url": self._page_url(p),
                "selector": selector,
                "status": "blocked",
                "reason": "security_verification",
                "message": SECURITY_VERIFICATION_MESSAGE,
                "candidates": [],
            }

        state = self._locator_state(locator)
        if state["visible"] and state["enabled"] and state["editable"] and state["bounding_box"]:
            try:
                locator.fill("" if clear_first else str(value))
                if clear_first:
                    locator.fill(str(value))
            except Exception as exc:
                candidates = self._visible_editable_candidates(p)
                return self._fill_failure(
                    tab_id=resolved_tab_id,
                    page=p,
                    selector=selector,
                    reason="fill_error",
                    state=state,
                    candidates=candidates,
                    message=str(exc),
                )
            return {"tab_id": resolved_tab_id, "url": self._page_url(p), "selector": selector, "status": "ok"}

        candidates = self._visible_editable_candidates(p)
        reason = "element_not_found" if state["count"] <= 0 else "element_not_visible"
        if len(candidates) == 1:
            try:
                # 只在唯一可见可编辑候选存在时使用真实键盘输入，避免把内容填到错误控件。
                self._keyboard_fill_candidate(p, candidates[0], str(value), clear_first=clear_first)
                return {
                    "tab_id": resolved_tab_id,
                    "url": self._page_url(p),
                    "selector": selector,
                    "filled_selector": candidates[0]["selector"],
                    "status": "ok",
                    "fallback_used": True,
                }
            except Exception as exc:
                return self._fill_failure(
                    tab_id=resolved_tab_id,
                    page=p,
                    selector=selector,
                    reason="fallback_fill_error",
                    state=state,
                    candidates=candidates,
                    message=str(exc),
                )
        return self._fill_failure(tab_id=resolved_tab_id, page=p, selector=selector, reason=reason, state=state, candidates=candidates)

    def type_text(self, selector: str, value: str, *, tab_id: str | None = None, delay_ms: int = 0, press_enter: bool = False) -> dict[str, Any]:
        resolved_tab_id, _, locator = self._locator(selector, tab_id)
        locator.type(str(value), delay=int(delay_ms))
        if press_enter:
            locator.press("Enter")
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "selector": selector, "status": "ok"}

    def press_key(self, key: str, *, selector: str | None = None, tab_id: str | None = None) -> dict[str, Any]:
        if selector:
            resolved_tab_id, _, locator = self._locator(selector, tab_id)
            locator.press(str(key))
        else:
            resolved_tab_id, p = self._resolve_tab(tab_id)
            p.keyboard.press(str(key))
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "key": str(key), "status": "ok"}

    def select_option(self, selector: str, values: Any, *, by: str = "value", tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, _, locator = self._locator(selector, tab_id)
        raw_values = values if isinstance(values, list) else [values]
        if by == "label":
            option = [{"label": str(v)} for v in raw_values]
        elif by == "index":
            option = [{"index": int(v)} for v in raw_values]
        else:
            option = [str(v) for v in raw_values]
        selected = locator.select_option(option)
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "selected": selected}

    def set_checkbox(self, selector: str, checked: bool, *, tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, _, locator = self._locator(selector, tab_id)
        locator.set_checked(bool(checked))
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "selector": selector, "checked": bool(checked)}

    def submit_form(self, selector: str, *, tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, _, locator = self._locator(selector, tab_id)
        locator.evaluate("(el) => { const form = el.tagName === 'FORM' ? el : el.closest('form'); if (!form) throw new Error('No form found'); if (form.requestSubmit) form.requestSubmit(); else form.submit(); }")
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "selector": selector, "status": "ok"}

    def hover(self, selector: str, *, tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, _, locator = self._locator(selector, tab_id)
        locator.hover()
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "selector": selector, "status": "ok"}

    def scroll(self, *, selector: str | None = None, x: int = 0, y: int = 800, to: str | None = None, tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, p = self._resolve_tab(tab_id)
        if selector:
            _, _, locator = self._locator(selector, resolved_tab_id)
            locator.scroll_into_view_if_needed()
        elif to == "top":
            p.evaluate("() => window.scrollTo(0, 0)")
        elif to == "bottom":
            p.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        else:
            p.mouse.wheel(int(x), int(y))
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "status": "ok"}

    def wait_for(self, *, selector: str | None = None, state: str = "visible", text: str | None = None, url_pattern: str | None = None, network_idle: bool = False, timeout_ms: int | None = None, tab_id: str | None = None) -> dict[str, Any]:
        started = datetime.now()
        resolved_tab_id, p = self._resolve_tab(tab_id)
        if selector:
            p.locator(selector).wait_for(state=state or "visible", timeout=timeout_ms or self.timeout_ms)
        if text:
            p.locator(f"text={text}").wait_for(state="visible", timeout=timeout_ms or self.timeout_ms)
        if url_pattern and not fnmatch.fnmatch(str(getattr(p, "url", "") or ""), url_pattern):
            p.wait_for_url(url_pattern, timeout=timeout_ms or self.timeout_ms)
        if network_idle:
            p.wait_for_load_state("networkidle", timeout=timeout_ms or self.timeout_ms)
        elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "matched": True, "elapsed_ms": elapsed_ms}

    def get_html(self, *, selector: str | None = None, max_chars: int = 50000, tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, p = self._resolve_tab(tab_id)
        html = p.locator(selector).inner_html() if selector else p.content()
        truncated = len(html) > int(max_chars)
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "html": html[: int(max_chars)], "truncated": truncated}

    def get_text(self, *, selector: str | None = None, max_chars: int = 50000, tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, p = self._resolve_tab(tab_id)
        text = p.locator(selector).inner_text() if selector else p.locator("body").inner_text()
        truncated = len(text) > int(max_chars)
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "text": text[: int(max_chars)], "truncated": truncated}

    def get_element(self, selector: str, *, tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, _, locator = self._locator(selector, tab_id)
        first = locator.first
        attrs = first.evaluate("(el) => Object.fromEntries(Array.from(el.attributes || []).map(a => [a.name, a.value]))")
        return {
            "tab_id": resolved_tab_id,
            "url": self.current_url(resolved_tab_id),
            "selector": selector,
            "tag": first.evaluate("(el) => el.tagName.toLowerCase()"),
            "text": _truncate_text(first.inner_text() if first else "", 1000),
            "attributes": attrs,
            "visible": first.is_visible(),
            "bounding_box": first.bounding_box(),
        }

    def query_elements(self, selector: str, *, limit: int = 20, tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, _, locator = self._locator(selector, tab_id)
        count = locator.count()
        items: list[dict[str, Any]] = []
        for idx in range(min(count, int(limit))):
            item = locator.nth(idx)
            items.append({
                "index": idx,
                "text": _truncate_text(item.inner_text() or "", 500),
                "visible": item.is_visible(),
                "bounding_box": item.bounding_box(),
            })
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "count": count, "items": items, "truncated": count > int(limit)}

    def extract(self, schema: dict[str, str], *, tab_id: str | None = None, max_chars: int = 5000) -> dict[str, Any]:
        if not isinstance(schema, dict) or not schema:
            raise ValueError("schema 必须为非空 object，形如 {field: selector}")
        resolved_tab_id, p = self._resolve_tab(tab_id)
        data: dict[str, str] = {}
        for field, selector in schema.items():
            try:
                data[str(field)] = _truncate_text(p.locator(str(selector)).inner_text(), int(max_chars))
            except Exception as exc:
                data[str(field)] = f"<error: {exc}>"
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "data": data}

    def console_logs(self, *, level: str | None = None, limit: int = 50, clear: bool = False, tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, _ = self._resolve_tab(tab_id)
        rows = list(self._console_logs.get(resolved_tab_id, []))
        if level:
            rows = [row for row in rows if str(row.get("type")) == str(level)]
        out = rows[-int(limit):]
        if clear:
            self._console_logs.get(resolved_tab_id, deque()).clear()
        return {"tab_id": resolved_tab_id, "logs": out, "count": len(out)}

    def page_errors(self, *, limit: int = 50, clear: bool = False, tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, _ = self._resolve_tab(tab_id)
        rows = list(self._page_errors.get(resolved_tab_id, []))[-int(limit):]
        if clear:
            self._page_errors.get(resolved_tab_id, deque()).clear()
        return {"tab_id": resolved_tab_id, "errors": rows, "count": len(rows)}

    def list_tabs(self) -> dict[str, Any]:
        return self.tab_snapshot()

    def _single_blank_tab_id(self) -> str | None:
        """Return the startup blank tab when it is still safe to reuse."""

        if len(self._tabs) != 1:
            return None
        tab_id, page = next(iter(self._tabs.items()))
        page_url = str(getattr(page, "url", "") or "")
        if page_url != "about:blank":
            return None
        try:
            title = _as_clean_text(page.title() or "")
        except Exception:
            title = ""
        return tab_id if not title else None

    def new_tab(self, url: str | None = None, *, make_active: bool = True) -> dict[str, Any]:
        self._ensure_started()
        tab_id = self._single_blank_tab_id()
        if tab_id is None:
            tab_id = self._register_page(self._context.new_page(), make_active=make_active)
        elif make_active:
            self._active_tab_id = tab_id
        if url:
            self.open_url(url, tab_id=tab_id)
        page = self._tabs[tab_id]
        return {"tab_id": tab_id, "url": str(getattr(page, "url", "") or ""), "title": _as_clean_text(page.title() or "")}

    def switch_tab(self, tab_id: str) -> dict[str, Any]:
        resolved_tab_id, page = self._resolve_tab(tab_id)
        self._active_tab_id = resolved_tab_id
        try:
            page.bring_to_front()
        except Exception:
            pass
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "title": _as_clean_text(page.title() or "")}

    def close_tab(self, tab_id: str | None = None) -> dict[str, Any]:
        self._ensure_started()
        resolved_tab_id = str(tab_id or self._active_tab_id)
        if resolved_tab_id not in self._tabs:
            raise ValueError(f"未知浏览器 tab_id: {resolved_tab_id}")
        if len(self._tabs) <= 1:
            raise ValueError("不能关闭最后一个浏览器标签页")
        page = self._tabs.pop(resolved_tab_id)
        page.close()
        self._console_logs.pop(resolved_tab_id, None)
        self._page_errors.pop(resolved_tab_id, None)
        if self._active_tab_id == resolved_tab_id:
            self._active_tab_id = next(iter(self._tabs))
        return {"closed_tab_id": resolved_tab_id, "active_tab_id": self._active_tab_id}

    def evaluate(self, expression: str, *, timeout_ms: int = 3000, max_chars: int = 20000, tab_id: str | None = None) -> dict[str, Any]:
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("expression 必须为非空字符串")
        resolved_tab_id, p = self._resolve_tab(tab_id)
        old_timeout = self.timeout_ms
        try:
            p.set_default_timeout(int(timeout_ms))
            value = p.evaluate(expression)
        finally:
            try:
                p.set_default_timeout(old_timeout)
            except Exception:
                pass
        safe_value, truncated = _json_safe(value, max_chars=int(max_chars))
        logger.info("browser_evaluate executed url=%s script_len=%d result_truncated=%s", self.current_url(resolved_tab_id), len(expression), truncated)
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "result": safe_value, "truncated": truncated}

    def get_cookies(self, *, urls: list[str] | None = None) -> dict[str, Any]:
        self._ensure_started()
        return {"cookies": self._context.cookies(urls or None)}

    def set_cookies(self, cookies: list[dict[str, Any]]) -> dict[str, Any]:
        self._ensure_started()
        if not isinstance(cookies, list):
            raise ValueError("cookies 必须为 list")
        self._context.add_cookies(cookies)
        return {"status": "ok", "count": len(cookies)}

    def clear_cookies(self) -> dict[str, Any]:
        self._ensure_started()
        self._context.clear_cookies()
        return {"status": "ok"}

    def get_storage(self, *, keys: list[str] | None = None, storage_type: str = "localStorage", tab_id: str | None = None) -> dict[str, Any]:
        resolved_tab_id, p = self._resolve_tab(tab_id)
        store = "sessionStorage" if storage_type == "sessionStorage" else "localStorage"
        script = """([storeName, keys]) => {
            const store = window[storeName];
            const out = {};
            const wanted = keys && keys.length ? keys : Object.keys(store);
            for (const key of wanted) out[key] = store.getItem(key);
            return out;
        }"""
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "storage_type": store, "items": p.evaluate(script, [store, list(keys or [])])}

    def set_storage(self, items: dict[str, Any], *, storage_type: str = "localStorage", tab_id: str | None = None) -> dict[str, Any]:
        if not isinstance(items, dict):
            raise ValueError("items 必须为 object")
        resolved_tab_id, p = self._resolve_tab(tab_id)
        store = "sessionStorage" if storage_type == "sessionStorage" else "localStorage"
        script = """([storeName, items]) => {
            const store = window[storeName];
            for (const [key, value] of Object.entries(items)) store.setItem(key, String(value));
        }"""
        p.evaluate(script, [store, items])
        return {"tab_id": resolved_tab_id, "url": self.current_url(resolved_tab_id), "storage_type": store, "count": len(items), "status": "ok"}

    def _safe_state_path(self, path: str | Path) -> Path:
        root = self.state_dir.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        resolved = (root / Path(path).name).resolve() if not Path(path).is_absolute() else Path(path).expanduser().resolve()
        if root != resolved and root not in resolved.parents:
            raise ValueError(f"storage_state 路径必须位于 {root}")
        return resolved

    def save_storage_state(self, path: str | Path = "storage_state.json") -> dict[str, Any]:
        self._ensure_started()
        out_path = self._safe_state_path(path)
        self._context.storage_state(path=str(out_path))
        return {"path": str(out_path), "status": "ok"}

    def load_storage_state(self, path: str | Path) -> dict[str, Any]:
        self._ensure_started()
        in_path = self._safe_state_path(path)
        if not in_path.exists():
            raise FileNotFoundError(f"storage_state 不存在: {in_path}")
        try:
            self._context.close()
        except Exception:
            pass
        self._new_context(storage_state=in_path)
        tab_id = self._register_page(self._context.new_page(), make_active=True)
        return {"path": str(in_path), "active_tab_id": tab_id, "status": "ok"}

    def start_recording(self, *, directory: str | Path | None = None) -> dict[str, Any]:
        self._ensure_started()
        if self._recording:
            raise ValueError("浏览器录屏已经启动")
        out_dir = Path(directory).expanduser() if directory else self.recording_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        self._recording_id = f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            self._context.close()
        except Exception:
            pass
        self._new_context(record_video_dir=out_dir)
        tab_id = self._register_page(self._context.new_page(), make_active=True)
        self._recording = True
        self._recording_path = out_dir
        return {"recording_id": self._recording_id, "active_tab_id": tab_id, "path": str(out_dir), "status": "recording"}

    def stop_recording(self, *, format: str = "webm", gif_path: str | Path | None = None) -> dict[str, Any]:
        if not self._recording:
            raise ValueError("浏览器录屏尚未启动")
        record_dir = self._recording_path or self.recording_dir
        try:
            self._context.close()
        finally:
            self._recording = False
            self._new_context()
            self._register_page(self._context.new_page(), make_active=True)
        webms = sorted(record_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
        webm_path = webms[0] if webms else record_dir
        if format == "gif":
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg and webm_path.is_file():
                out_gif = Path(gif_path).expanduser() if gif_path else webm_path.with_suffix(".gif")
                subprocess.run([ffmpeg, "-y", "-i", str(webm_path), "-vf", "fps=10,scale=720:-1:flags=lanczos", str(out_gif)], check=False, capture_output=True, text=True)
                if out_gif.exists():
                    return {"path": str(out_gif), "format": "gif", "webm_path": str(webm_path)}
            return {"path": str(webm_path), "format": "webm", "warning": "ffmpeg 不可用或转换失败，返回 webm"}
        return {"path": str(webm_path), "format": "webm"}


def _browser_thread_method(method: Callable[..., Any]) -> Callable[..., Any]:
    """Marshal public Playwright operations through the session owner thread."""

    if getattr(method, "_juice_thread_bound", False):
        return method

    def wrapped(self: PlaywrightBrowserSession, *args: Any, **kwargs: Any) -> Any:
        return self.run_on_browser_thread(lambda: method(self, *args, **kwargs))

    wrapped.__name__ = getattr(method, "__name__", "wrapped")
    wrapped.__doc__ = getattr(method, "__doc__", None)
    wrapped._juice_thread_bound = True  # type: ignore[attr-defined]
    return wrapped


for _thread_bound_name in (
    "reconfigure",
    "page",
    "current_url",
    "tab_snapshot",
    "status",
    "open_url",
    "go_back",
    "close_popups",
    "find_text",
    "screenshot",
    "search_detailed",
    "search",
    "click",
    "fill",
    "type_text",
    "press_key",
    "select_option",
    "set_checkbox",
    "submit_form",
    "hover",
    "scroll",
    "wait_for",
    "get_html",
    "get_text",
    "get_element",
    "query_elements",
    "extract",
    "console_logs",
    "page_errors",
    "list_tabs",
    "new_tab",
    "switch_tab",
    "close_tab",
    "evaluate",
    "get_cookies",
    "set_cookies",
    "clear_cookies",
    "get_storage",
    "set_storage",
    "save_storage_state",
    "load_storage_state",
    "start_recording",
    "stop_recording",
):
    setattr(
        PlaywrightBrowserSession,
        _thread_bound_name,
        _browser_thread_method(getattr(PlaywrightBrowserSession, _thread_bound_name)),
    )


_BROWSER_SESSIONS: dict[str, PlaywrightBrowserSession] = {}


def get_browser_session(session_key: str = "default", **kwargs: Any) -> PlaywrightBrowserSession:
    key = str(session_key or "default")
    session = _BROWSER_SESSIONS.get(key)
    if session is None:
        session = PlaywrightBrowserSession(**kwargs)
        _BROWSER_SESSIONS[key] = session
    else:
        session.reconfigure(**kwargs)
    return session


def get_existing_browser_session(session_key: str = "default") -> PlaywrightBrowserSession | None:
    """Return a browser session only if a tool or gateway has already created it."""

    return _BROWSER_SESSIONS.get(str(session_key or "default"))


__all__ = [
    "DEFAULT_BROWSER_RECORDINGS_DIR",
    "DEFAULT_BROWSER_SCREENSHOTS_DIR",
    "DEFAULT_BROWSER_STATE_DIR",
    "DEFAULT_USER_AGENT",
    "PlaywrightBrowserSession",
    "_format_playwright_start_error",
    "_parse_version_tuple",
    "get_existing_browser_session",
    "get_browser_session",
]
