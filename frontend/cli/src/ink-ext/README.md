# ink-ext

> [简体中文](README.zh-CN.md)

`ink-ext` uses Ink's public API for the terminal alternate screen and row-level virtual scrolling without modifying Ink. See the [CLI README](../../README.md) for the parent UI and test entry points.

| Module | Responsibility |
| --- | --- |
| `AlternateScreen.tsx` | Enter and leave the alternate screen with DEC 1049 |
| `VirtualScrollList.tsx` | Slice visual rows by viewport and follow the bottom as content grows |
| `useScrollKeys.ts` | Map scroll keys to viewport movement; defaults to `PgUp` / `PgDn` |

## Development conventions

`AlternateScreen` uses `useInsertionEffect` so the alternate screen is active before the first frame. The root `Box` is not fixed to terminal height; the parent reserves space from terminal height to avoid Ink clearing the screen when output reaches the row limit.

Callers first convert messages into one-row, stable-key `RowVM[]` values. `VirtualScrollList` renders only rows in `[scrollTop, scrollTop + viewportHeight)`. Scrolling upward disables auto-follow; jumping or returning to the bottom restores it. Do not rely on Ink's `overflow:hidden` to crop rows or embed ANSI color codes in `<Text>`.

Ink broadcasts keys to every active `useInput` listener. Use only `PgUp` / `PgDn` by default so scrolling does not conflict with input characters or cursor movement.

Run regression tests from `frontend/cli`:

```bash
npx tsx --test src/ink-ext/*.test.ts
npm run typecheck
```
