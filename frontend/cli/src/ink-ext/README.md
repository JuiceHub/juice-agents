# ink-ext

`ink-ext` 使用 Ink 的公开 API 实现终端备用屏和行级虚拟滚动，不修改 Ink 源码。
上层界面及测试入口见 [CLI README](../../README.md)。

| 模块 | 职责 |
| --- | --- |
| `AlternateScreen.tsx` | 以 DEC 1049 进入/退出备用屏 |
| `VirtualScrollList.tsx` | 根据视口截取视觉行，并在内容增长时跟随底部 |
| `useScrollKeys.ts` | 把滚动按键映射为视口移动；默认只接收 `PgUp` / `PgDn` |

## 开发约定

`AlternateScreen` 使用 `useInsertionEffect`，确保首帧绘制前已经进入备用屏。
根 `Box` 不固定为终端行高；上层按终端高度预留空间，避免 Ink 在输出达到行高时清屏。

调用方先把消息转换为一行一个、带稳定 key 的 `RowVM[]`；
`VirtualScrollList` 只渲染 `[scrollTop, scrollTop + viewportHeight)` 的行。
用户上滚后停止自动贴底，跳到底部或滚回底部时恢复。
不要依赖 Ink 的 `overflow:hidden` 裁剪部分行，也不要把 ANSI 色码嵌入 `<Text>`。

Ink 会向所有激活的 `useInput` 监听器广播按键。
默认滚动键位仅使用 `PgUp` / `PgDn`，避免与输入框的字符和光标操作冲突。

在 `frontend/cli` 下运行回归测试：

```bash
npx tsx --test src/ink-ext/*.test.ts
npm run typecheck
```
