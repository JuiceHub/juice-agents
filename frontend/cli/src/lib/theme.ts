/**
 * Shared visual tokens and UI copy for the TypeScript Ink CLI.
 *
 * The refreshed interface follows a quieter Claude-style transcript with a
 * branded Juice welcome card at the top of the conversation.
 */

export const TUI_THEME = {
  brand: {
    eyebrow: "#8a7a5c",
    wordmark: "#f5d18b",
    meta: "#d7a84d",
    guide: "#5d4724",
    glow: "#7bc4d6",
  },
  surface: {
    text: "#f3ead8",
    muted: "#8a7a5c",
    subtle: "#6a5a44",
  },
  composer: {
    prompt: "#f5d18b",
    text: "#f3ead8",
    placeholder: "#80715a",
    cursor: "#f5d18b",
    ghost: "#6fa9b7",
    overlayText: "#f3ead8",
    overlayHint: "#8a7a5c",
    overlaySelected: "#f5d18b",
  },
  completion: {
    hint: "#8a7a5c",
    text: "#f3ead8",
    selected: "#f5d18b",
    marker: "#7bc4d6",
  },
  message: {
    assistantAccent: "#f5d18b",
    userAccent: "#7bc4d6",
    thinkingAccent: "#8a7a5c",
    responseAccent: "#d7a84d",
    errorAccent: "#ff8a7a",
    body: "#f3ead8",
    muted: "#c8b99a",
    divider: "#8a7a5c",
  },
  state: {
    success: "#89c79b",
    warning: "#d7a84d",
    danger: "#ff8a7a",
  },
  // Shimmer (浅色高光) 用于 Spinner 扫光效果。每一个 status 对应一个浅版色，
  // shimmer 算法在文字索引上从左向右移动，命中字符使用对应的浅色，营造光泽感。
  shimmer: {
    assistant: "#fff1c0",
    success: "#b8e2c5",
    danger: "#ffb5ab",
    warning: "#e8c378",
  },
  // 工具/状态语义色：StatusIcon 组件根据这一组色板上色。命名与 claude-code 对齐
  // (success/error/warning/info/pending/running)，但取值仍走 Juice 土金色调。
  status: {
    success: "#89c79b",
    error: "#ff8a7a",
    warning: "#d7a84d",
    info: "#7bc4d6",
    pending: "#8a7a5c",
    running: "#f5d18b",
  },
} as const;

export const TUI_COPY = {
  placeholder: "Ask for code, docs, or a workflow change",
  welcomeWordmark: "JUICE AGENTS",
  welcomeBannerLines: [
    "     ██╗██╗   ██╗██╗ ██████╗███████╗",
    "     ██║██║   ██║██║██╔════╝██╔════╝",
    "     ██║██║   ██║██║██║     █████╗",
    "██   ██║██║   ██║██║██║     ██╔══╝",
    "╚█████╔╝╚██████╔╝██║╚██████╗███████╗",
    " ╚════╝  ╚═════╝ ╚═╝ ╚═════╝╚══════╝",
    "",
    "      █████╗  ██████╗ ███████╗███╗   ██╗████████╗███████╗",
    "     ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝██╔════╝",
    "     ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   ███████╗",
    "     ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   ╚════██║",
    "     ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   ███████║",
    "     ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚══════╝",
  ],
  welcomeBannerGradient: [
    "#ffe7a3",
    "#f8d889",
    "#efc96f",
    "#e4b955",
    "#d8a947",
    "#bf8735",
  ],
  welcomeTagline:
    "A lightweight terminal surface for shipping changes, reading code, and steering agents without losing flow.",
  booting: "Starting session...",
  currentWorkStatus: "Thinking... (Esc/Ctrl+C to stop)",
  completionHint: "Tab accept · Esc close",
  composerHint: "Enter send · Alt+Enter newline · Tab complete · Shift+Tab mode",
  newMessages: "new messages",
  jumpToEnd: "End jump",
  unknownModel: "unknown-model",
  // accept 可跨会话持久化，恢复时必须提示当前不再逐次审批。
  acceptModeNotice: "accept mode · writes and commands run without asking (/permissions default to restore)",
  emptyTranscript:
    "The session is ready. Start with a task, a slash command, or a repo question.",
  introTitle: "JUICE AGENTS",
} as const;
