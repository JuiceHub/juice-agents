import React, { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, ExternalLink, Globe2, Plus, RefreshCcw, X } from "lucide-react";
import type { BrowserPreview as BrowserPreviewType } from "@juice-agents/shared/gateway/types";
import type { BrowserTabState } from "../lib/layout.js";

type BrowserLiveServerMessage = Partial<BrowserPreviewType> & {
  type: "frame" | "status" | "error";
  data?: string;
  url?: string;
  title?: string;
  width?: number;
  height?: number;
  protocol?: number;
  can_go_back?: boolean;
  can_go_forward?: boolean;
  message?: string;
};

export function BrowserPreview(props: {
  preview: BrowserPreviewType | null;
  liveSocketUrl: string;
  liveEnabled: boolean;
  tabs: BrowserTabState[];
  activeTabId: string;
  url?: string;
  browserTabId?: string;
  imageUrl?: string;
  reloadKey: number;
  canGoBack: boolean;
  canGoForward: boolean;
  onAddBrowserTab: () => void;
  onLiveStatus?: (preview: BrowserPreviewType) => void;
  onUrlChange?: (url: string) => void;
  onBack?: () => void;
  onForward?: () => void;
  onRefresh?: () => void;
  onOpenExternal?: (url?: string) => Promise<void>;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const url = props.url || props.preview?.url || "";
  const [draftUrl, setDraftUrl] = useState(url);
  const [streamUrl, setStreamUrl] = useState(url);
  const [streamTitle, setStreamTitle] = useState("");
  const [streamError, setStreamError] = useState("");
  const [liveProtocol, setLiveProtocol] = useState(1);
  const [serverCanGoBack, setServerCanGoBack] = useState<boolean | null>(null);
  const [serverCanGoForward, setServerCanGoForward] = useState<boolean | null>(null);
  const [externalMode, setExternalMode] = useState(Boolean(props.preview?.external_window));
  const [openingExternal, setOpeningExternal] = useState(false);
  const [hasFrame, setHasFrame] = useState(false);
  const [frameSize, setFrameSize] = useState(props.preview?.viewport || { width: 1280, height: 900 });

  useEffect(() => {
    setDraftUrl(streamUrl || url);
  }, [url, streamUrl]);

  useEffect(() => {
    if (props.preview?.external_window) {
      setExternalMode(true);
    }
  }, [props.preview?.external_window]);

  useEffect(() => {
    if (!props.liveEnabled) {
      socketRef.current?.close();
      socketRef.current = null;
      return;
    }
    // The component remains mounted for page-tab switches, so existing canvas
    // pixels stay visible until the replacement screencast's first frame.
    // PreviewPanel remounts this component when the runner socket URL changes.
    const socket = new WebSocket(props.liveSocketUrl);
    socketRef.current = socket;
    setStreamError("");
    setLiveProtocol(1);
    setServerCanGoBack(null);
    setServerCanGoForward(null);
    socket.onopen = () => {
      if (props.browserTabId) {
        socket.send(JSON.stringify({ type: "switch_tab", tab_id: props.browserTabId }));
      }
    };
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as BrowserLiveServerMessage;
      if (message.type === "error") {
        setStreamError(message.message || "Browser stream failed");
        return;
      }
      if (message.url !== undefined) {
        setStreamUrl(message.url || "");
      }
      if (message.title !== undefined) {
        setStreamTitle(message.title || "");
      }
      if (message.protocol !== undefined) {
        setLiveProtocol(Number(message.protocol) || 1);
      }
      if (message.can_go_back !== undefined) {
        setServerCanGoBack(Boolean(message.can_go_back));
      }
      if (message.can_go_forward !== undefined) {
        setServerCanGoForward(Boolean(message.can_go_forward));
      }
      if (message.type === "status") {
        props.onLiveStatus?.({
          connected: message.connected ?? true,
          title: message.title || "",
          url: message.url || "",
          image_url: message.image_url || "",
          session_key: message.session_key,
          active_tab_id: message.active_tab_id,
          tabs: message.tabs || [],
          can_go_back: message.can_go_back,
          can_go_forward: message.can_go_forward,
          external_window: message.external_window,
          headless: message.headless,
          viewport: message.viewport,
          stream_state: message.stream_state,
        });
      }
      if (message.type === "frame" && message.data) {
        drawFrame(message.data, message.width || frameSize.width, message.height || frameSize.height);
      }
    };
    socket.onerror = () => setStreamError("Browser stream disconnected");
    socket.onclose = () => {
      if (socketRef.current === socket) {
        socketRef.current = null;
      }
    };
    return () => {
      socketRef.current = null;
      socket.close();
    };
  }, [props.liveEnabled, props.liveSocketUrl, props.browserTabId]);

  function drawFrame(data: string, width: number, height: number): void {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const image = new Image();
    image.onload = () => {
      canvas.width = image.naturalWidth || width;
      canvas.height = image.naturalHeight || height;
      setFrameSize({ width: canvas.width, height: canvas.height });
      setHasFrame(true);
      canvas.getContext("2d")?.drawImage(image, 0, 0, canvas.width, canvas.height);
    };
    image.src = `data:image/jpeg;base64,${data}`;
  }

  function sendBrowserCommand(command: Record<string, unknown>): void {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify(command));
  }

  function eventPoint(
    event: React.PointerEvent<HTMLCanvasElement> | React.MouseEvent<HTMLCanvasElement> | React.WheelEvent<HTMLCanvasElement>
  ) {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * frameSize.width,
      y: ((event.clientY - rect.top) / rect.height) * frameSize.height,
    };
  }

  function buttonName(button: number): "left" | "right" | "middle" {
    if (button === 1) return "middle";
    if (button === 2) return "right";
    return "left";
  }

  function keyCommand(event: React.KeyboardEvent<HTMLCanvasElement>): Record<string, unknown> {
    const modifiers = [
      event.ctrlKey ? "Control" : "",
      event.metaKey ? "Meta" : "",
      event.altKey ? "Alt" : "",
      event.shiftKey ? "Shift" : "",
    ].filter(Boolean);
    if (event.key.length === 1 && modifiers.length === 0) {
      return { type: "key", text: event.key };
    }
    const keyMap: Record<string, string> = {
      ArrowUp: "ArrowUp",
      ArrowDown: "ArrowDown",
      ArrowLeft: "ArrowLeft",
      ArrowRight: "ArrowRight",
      Backspace: "Backspace",
      Delete: "Delete",
      Enter: "Enter",
      Escape: "Escape",
      Tab: "Tab",
      " ": "Space",
    };
    return { type: "key", key: keyMap[event.key] || event.key, modifiers };
  }

  function submitUrl(value: string): void {
    props.onUrlChange?.(value);
    sendBrowserCommand({ type: "navigate", url: value });
  }

  function goBack(): void {
    props.onBack?.();
    sendBrowserCommand({ type: "back" });
  }

  function goForward(): void {
    props.onForward?.();
    sendBrowserCommand({ type: "forward" });
  }

  function refresh(): void {
    props.onRefresh?.();
    sendBrowserCommand({ type: "refresh" });
  }

  function selectTab(tab: BrowserTabState): void {
    if (tab.id === props.activeTabId) return;
    sendBrowserCommand({ type: "switch_tab", tab_id: tab.browserTabId });
  }

  function closeTab(tab: BrowserTabState): void {
    if (props.tabs.length <= 1) return;
    sendBrowserCommand({ type: "close_tab", tab_id: tab.browserTabId });
  }

  async function openExternal(): Promise<void> {
    setOpeningExternal(true);
    try {
      await props.onOpenExternal?.(displayUrl || draftUrl);
      setExternalMode(true);
    } finally {
      setOpeningExternal(false);
    }
  }

  const displayUrl = streamUrl || url;
  const title = streamTitle || props.preview?.title || "Browser";
  const fallbackImage = props.imageUrl || props.preview?.image_url || "";
  const canGoBack = serverCanGoBack ?? props.canGoBack;
  const canGoForward = serverCanGoForward ?? props.canGoForward;

  return (
    <section className="browser-preview">
      <div className="browser-tab-strip" role="tablist" aria-label="Browser pages">
        <div className="browser-tab-scroll">
          {props.tabs.map((tab) => {
            const active = tab.id === props.activeTabId;
            return (
              <div className={`browser-page-tab ${active ? "active" : ""}`} key={tab.id}>
                <button
                  type="button"
                  role="tab"
                  aria-selected={active}
                  className="browser-page-tab-main"
                  title={tab.url || tab.title}
                  onClick={() => selectTab(tab)}
                >
                  <Globe2 size={13} />
                  <span>{tab.title || "Browser"}</span>
                </button>
                <button
                  type="button"
                  className="browser-page-tab-close"
                  title={props.tabs.length <= 1 ? "Keep at least one browser tab" : `Close ${tab.title || "browser tab"}`}
                  disabled={props.tabs.length <= 1}
                  onClick={() => closeTab(tab)}
                >
                  <X size={12} />
                </button>
              </div>
            );
          })}
        </div>
        <button type="button" className="browser-tab-add" title="Add browser tab" onClick={props.onAddBrowserTab}>
          <Plus size={14} />
        </button>
      </div>
      <form
        className="browser-toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          const value = String(new FormData(event.currentTarget).get("url") || "").trim();
          if (value) {
            submitUrl(value);
          }
        }}
      >
        <div className="browser-nav-buttons">
          <button type="button" onClick={goBack} disabled={!canGoBack} title="Back">
            <ArrowLeft size={14} />
          </button>
          <button type="button" onClick={goForward} disabled={!canGoForward} title="Forward">
            <ArrowRight size={14} />
          </button>
          <button type="button" onClick={refresh} title="Refresh">
            <RefreshCcw size={14} />
          </button>
        </div>
        <input
          className="address-bar"
          name="url"
          value={draftUrl}
          onChange={(event) => setDraftUrl(event.target.value)}
          placeholder="Enter a URL"
          aria-label="Browser URL"
        />
        <button
          type="button"
          disabled={!displayUrl}
          title="Open externally"
          onClick={() => {
            if (displayUrl) window.open(displayUrl, "_blank", "noopener,noreferrer");
          }}
        >
          <ExternalLink size={14} />
        </button>
        <button
          type="button"
          title="Open interactive browser"
          disabled={openingExternal}
          onClick={() => void openExternal()}
        >
          <Globe2 size={14} />
        </button>
      </form>
      <div className="browser-viewport">
        <div className="browser-frame-stack">
          {externalMode ? (
            <div className="external-browser-panel">
              <Globe2 size={28} />
              <strong>Interactive Chromium window</strong>
              <span>{displayUrl || "Ready for direct browser interaction."}</span>
              <button type="button" onClick={() => void openExternal()} disabled={openingExternal}>
                {openingExternal ? "Opening..." : "Focus browser"}
              </button>
            </div>
          ) : (
            <>
              <canvas
                ref={canvasRef}
                className="browser-canvas"
                tabIndex={0}
                aria-label={title}
                onPointerDown={(event) => {
                  event.currentTarget.focus();
                  event.currentTarget.setPointerCapture(event.pointerId);
                  if (liveProtocol >= 2) {
                    sendBrowserCommand({ type: "mouse", event: "down", ...eventPoint(event), button: buttonName(event.button) });
                  }
                }}
                onPointerUp={(event) => {
                  const point = eventPoint(event);
                  if (liveProtocol >= 2) {
                    sendBrowserCommand({ type: "mouse", event: "up", ...point, button: buttonName(event.button) });
                  } else {
                    sendBrowserCommand({ type: "click", ...point, button: buttonName(event.button) });
                  }
                }}
                onPointerMove={(event) => {
                  const point = eventPoint(event);
                  sendBrowserCommand(liveProtocol >= 2 ? { type: "mouse", event: "move", ...point } : { type: "move", ...point });
                }}
                onDoubleClick={(event) => {
                  sendBrowserCommand({ type: "mouse", event: "dblclick", ...eventPoint(event), button: buttonName(event.button) });
                }}
                onContextMenu={(event) => event.preventDefault()}
                onWheel={(event) => {
                  event.preventDefault();
                  sendBrowserCommand({ type: "wheel", ...eventPoint(event), delta_x: event.deltaX, delta_y: event.deltaY });
                }}
                onKeyDown={(event) => {
                  event.preventDefault();
                  sendBrowserCommand(keyCommand(event));
                }}
                onPaste={(event) => {
                  event.preventDefault();
                  sendBrowserCommand({ type: "paste", text: event.clipboardData.getData("text") });
                }}
              />
              {!hasFrame && fallbackImage ? <img className="browser-fallback-image" src={fallbackImage} alt={title} /> : null}
            </>
          )}
          <div className="browser-frame-help">
            <span>{streamError || "Live Chromium"}</span>
            <button
              type="button"
              disabled={!displayUrl}
              onClick={() => displayUrl && window.open(displayUrl, "_blank", "noopener,noreferrer")}
            >
              <ExternalLink size={13} />
              <span>Open externally</span>
            </button>
          </div>
          {!displayUrl && !fallbackImage ? <LocalBrowserHome /> : null}
        </div>
      </div>
    </section>
  );
}

function LocalBrowserHome() {
  const origin = window.location.origin;
  return (
    <div className="local-browser-home">
      <div className="local-heading">Local</div>
      <button className="local-site-card" onClick={() => window.open(origin, "_blank", "noopener,noreferrer")}>
        <div className="local-site-thumb">
          <Globe2 size={20} />
        </div>
        <span>
          <strong>Juice Web</strong>
          <small>{origin.replace(/^https?:\/\//, "")}</small>
        </span>
        <i aria-label="Available" />
      </button>
    </div>
  );
}
