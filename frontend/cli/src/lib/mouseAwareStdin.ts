/**
 * mouseAwareStdin —— 在 process.stdin 与 Ink 之间插入一层 SGR mouse 序列过滤器。
 *
 * 问题
 * -----
 * Alt-screen 里启用 SGR mouse tracking（DEC 1006）后，鼠标滚轮/点击会以
 * `\x1b[<{btn};{x};{y}{M|m}` 序列写入 stdin。Ink 的 keypress parser 不识别这种
 * 扩展序列，会把整段当成「未识别 escape」并以 `input="[<64;10;5M"` 这种字符串
 * 形式通过 useInput 广播给所有监听器——结果 RichComposer 把 mouse 字节当文本
 * 插入输入框，体验全乱。
 *
 * 解法
 * -----
 * 用 PassThrough 作为给 Ink 的 stdin 替身：
 *   - 监听真实 process.stdin 的 data 事件
 *   - 用状态机解析 SGR mouse 序列，wheel 事件 emit 出去（供 useMouseWheel 订阅）
 *   - 非 mouse 字节原样 write 进 PassThrough → Ink 通过 read() 拿到的就是干净字节
 *   - 转发 setRawMode / isTTY / ref / unref，让 Ink 像用普通 TTY stdin 一样
 *
 * 为什么不用 ink 的 useInput parser 改造：parser 在 ink 内部，改它要 fork。
 * 在 stdin 边界拦截是最隔离、最稳的做法，不依赖 ink 内部实现。
 */

import { PassThrough } from "node:stream";
import { EventEmitter } from "node:events";

/** Wheel 事件方向。 */
export type WheelDirection = "up" | "down";

/** Mouse SGR 序列产出的 wheel 事件。 */
export interface WheelEvent {
  direction: WheelDirection;
  /** 终端坐标（1-based）；当前未使用，保留供未来按区域分发。 */
  x: number;
  y: number;
  /** 是否带 shift 修饰键（btn 高位）。 */
  shift: boolean;
}

export interface MouseAwareStdinHandle {
  /** 给 Ink 的 stdin 替身（PassThrough，过滤后字节流）。 */
  stdin: NodeJS.ReadStream;
  /** Wheel/mouse 事件订阅入口。事件名固定为 "wheel"。 */
  emitter: EventEmitter;
  /** 卸载所有 listener，恢复原状（不关闭 process.stdin）。 */
  dispose: () => void;
}

/**
 * 包装 process.stdin，返回过滤掉 SGR mouse 序列的替身流 + wheel 事件 emitter。
 */
export function createMouseAwareStdin(
  source: NodeJS.ReadStream
): MouseAwareStdinHandle {
  const proxy = new PassThrough();
  const emitter = new EventEmitter();

  // 跨 chunk 的解析缓冲：当一段 mouse 序列被 OS 切到两个 chunk 时用。
  let pending = "";

  const onData = (chunk: Buffer | string): void => {
    // 用 binary 编码做 SGR 序列识别（避免 UTF-8 多字节被拆），但写回时恢复 Buffer。
    const buf = typeof chunk === "string" ? Buffer.from(chunk, "utf8") : chunk;
    const text = buf.toString("binary");
    const combined = pending + text;
    const { clean, leftover, wheels } = parseMouseSequences(combined);
    pending = leftover;
    for (const w of wheels) {
      emitter.emit("wheel", w);
    }
    if (clean.length > 0) {
      // 关键：用 latin1 (即 binary) 把字节序列还原成 Buffer，保留 UTF-8 原始字节。
      // 不能用 'utf8'，否则单字节会被当成 UTF-8 重新编码，破坏中文。
      proxy.write(Buffer.from(clean, "latin1"));
    }
  };

  source.on("data", onData);

  // Ink 期望的 TTY 接口转发到真实 stdin。
  // 注：proxy 本身不是 TTY，但 Ink 主要用 isTTY / setRawMode / ref / unref / setEncoding。
  const proxyAsTTY = proxy as unknown as NodeJS.ReadStream;
  Object.defineProperty(proxyAsTTY, "isTTY", {
    get: () => Boolean((source as any).isTTY),
    configurable: true,
  });
  proxyAsTTY.setRawMode = ((mode: boolean) => {
    if (typeof source.setRawMode === "function") {
      source.setRawMode(mode);
    }
    return proxyAsTTY;
  }) as NodeJS.ReadStream["setRawMode"];
  // Ink 会调用 stdin.setEncoding('utf8')；PassThrough 原生支持但需显式转发给真实 stdin
  const originalSetEncoding = proxy.setEncoding.bind(proxy);
  proxyAsTTY.setEncoding = ((encoding: BufferEncoding) => {
    originalSetEncoding(encoding);
    if (typeof (source as any).setEncoding === "function") {
      (source as any).setEncoding(encoding);
    }
    return proxyAsTTY;
  }) as NodeJS.ReadStream["setEncoding"];
  proxyAsTTY.ref = () => {
    source.ref?.();
    return proxyAsTTY;
  };
  proxyAsTTY.unref = () => {
    source.unref?.();
    return proxyAsTTY;
  };

  const dispose = () => {
    source.off("data", onData);
    proxy.end();
  };

  return { stdin: proxyAsTTY, emitter, dispose };
}

interface ParseResult {
  /** 过滤掉 mouse 序列后的纯净字节流。 */
  clean: string;
  /** 末尾未完成的部分（下一个 chunk 拼接后再解析）。 */
  leftover: string;
  /** 本次解析到的 wheel 事件。 */
  wheels: WheelEvent[];
}

/**
 * 状态机解析输入流中的 SGR mouse 序列（`\x1b[<{btn};{x};{y}{M|m}`）。
 *
 * - 同时识别 mouse press / release / drag / wheel；只把 wheel(btn=64/65) 转成事件
 * - 其他 mouse 事件（点击/拖动）也从字节流中剥离，避免泄漏到 ink 当字符
 * - 末尾若是不完整序列（如刚到 `\x1b[<64;`），整段进 leftover，下次拼接
 *
 * 纯函数（无状态、可单测）。
 */
export function parseMouseSequences(input: string): ParseResult {
  let clean = "";
  const wheels: WheelEvent[] = [];
  let i = 0;

  while (i < input.length) {
    const esc = input.indexOf("\x1b[<", i);
    if (esc === -1) {
      // 没有 mouse 序列起点：剩余全是干净字节
      clean += input.slice(i);
      i = input.length;
      break;
    }
    // 把 mouse 序列起点之前的字节归为干净
    clean += input.slice(i, esc);

    // 找到序列终止符 M / m
    let end = -1;
    for (let j = esc + 3; j < input.length; j++) {
      const ch = input[j];
      if (ch === "M" || ch === "m") {
        end = j;
        break;
      }
      // 序列只允许数字、分号；遇到其他字节说明误判，回退把 ESC 当普通字符吐出
      if (ch !== ";" && (ch < "0" || ch > "9")) {
        end = -2; // 标记误判
        break;
      }
    }

    if (end === -1) {
      // 末尾不完整：整段进 leftover
      return { clean, leftover: input.slice(esc), wheels };
    }
    if (end === -2) {
      // 误判：把 ESC 字节作为普通字符吐出，从下一字节继续找
      clean += input[esc];
      i = esc + 1;
      continue;
    }

    // 解析参数 `{btn};{x};{y}`
    const params = input.slice(esc + 3, end).split(";");
    if (params.length === 3) {
      const btn = Number(params[0]);
      const x = Number(params[1]);
      const y = Number(params[2]);
      if (Number.isFinite(btn) && Number.isFinite(x) && Number.isFinite(y)) {
        // SGR mouse btn 编码：低 2 位为按键，bit 6(64) 表示 wheel
        // wheel up=64, wheel down=65（高位含 shift/meta/ctrl 修饰）
        const isWheel = (btn & 64) !== 0;
        if (isWheel) {
          // 仅在 press（M）时产出，避免重复（SGR 模式 wheel 通常只发 M）
          if (input[end] === "M") {
            wheels.push({
              direction: (btn & 1) === 0 ? "up" : "down",
              x,
              y,
              shift: (btn & 4) !== 0,
            });
          }
        }
        // 其他 mouse 事件（点击/拖动/释放）也从字节流中吞掉，不泄漏给 ink
      }
    }

    i = end + 1;
  }

  return { clean, leftover: "", wheels };
}

/** 启用 SGR mouse tracking 的终端控制序列（DEC 1000 + 1006）。 */
export const ENABLE_MOUSE_TRACKING = "\x1b[?1000h\x1b[?1006h";
/** 禁用 SGR mouse tracking。 */
export const DISABLE_MOUSE_TRACKING = "\x1b[?1006l\x1b[?1000l";
