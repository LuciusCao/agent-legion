/**
 * 模块级抽屉栈（#812 codex P2）：Studio 右侧两个 persistent Drawer
 * （节点详情 + 共享素材）可同时打开，且各自在 document capture 阶段挂 Esc
 * 监听——没有栈的话先注册的监听器先 preventDefault，栈序与视觉层级脱钩
 * （先开的共享素材在下层却先吃掉 Esc）。实例打开入栈、关闭出栈，
 * `useDrawerEscape` 只在自己是栈顶（最近打开、视觉最上层）时消费 Esc。
 * 与 Dock 栈（features/agentPanelDock/dockStack.ts）同款结构：栈位同时映射
 * 视觉层级（#812 对抗轮 D2——z-index = 1200 + 栈位，钳 1289：上方依次是
 * Toast 1290 与 MUI Modal 1300，刻度见 lib/zLayers.ts（#818）；Esc 栈序 ==
 * 视觉序）。suppressed（隐藏态）的实例不入栈：隐藏
 * 抽屉占着栈顶会挡住可见抽屉的 Esc（同 Dock hidden 语义）。
 */

import { Z_LAYERS } from '../../../lib/zLayers'

// symbol 做实例身份：跨模块唯一、不可伪造。
const stack: symbol[] = []

// 轻量订阅（同 dockStack）：实例经 useSyncExternalStore 跟随自己的栈位变化。
const listeners = new Set<() => void>()

function emitChange(): void {
  for (const listener of listeners) listener()
}

export function drawerStackSubscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** 入栈/置顶（幂等——已存在先移除再压顶；重新打开语义上就是最前）。 */
export function drawerStackRaise(id: symbol): void {
  const index = stack.indexOf(id)
  if (index !== -1) stack.splice(index, 1)
  stack.push(id)
  emitChange()
}

/** 出栈（关闭/unmount/suppressed）。 */
export function drawerStackRemove(id: symbol): void {
  const index = stack.indexOf(id)
  if (index === -1) return
  stack.splice(index, 1)
  emitChange()
}

/** 自己是否栈顶（Esc 消费门槛）。 */
export function drawerStackIsTop(id: symbol): boolean {
  return stack[stack.length - 1] === id
}

/** 栈位映射 z-index：1200 + 栈位，钳到 1289——必须低于 Toast（#818：瞬时
 * 反馈压过抽屉）与 MUI Modal（模态浮层永远压抽屉），栈深超过上限时不再
 * 继续抬。 */
export function drawerStackZIndex(id: symbol): number {
  const { studioDrawerBase: base, studioDrawerMax: max } = Z_LAYERS
  const index = stack.indexOf(id)
  if (index === -1) return base
  return Math.min(base + index, max)
}
