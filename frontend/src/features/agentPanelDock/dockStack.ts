/**
 * 模块级 Dock 栈（#801 codex P1）：同页可同时开多个 Dock（如 job detail 的
 * 定制预览 + 排查），每个实例都在 document 挂 Esc 监听——没有栈的话一次
 * Esc 关掉全部（丢未发送文本、定制预览草稿授权失效）。实例 mount 入栈、
 * unmount/关闭出栈，pointerdown/focusin 交互时置顶；`useDockEscape` 只在
 * 自己是栈顶时消费 Esc。栈位同时映射视觉层级（z-index = 900 + 栈位，钳
 * 999 低于 Toast，刻度见 lib/zLayers.ts，#801 codex 轮 2）——被点击抬栈的 Dock 同步抬到最
 * 上层，Esc 栈序与视觉层级一致。hidden（suppressed）的实例不入栈：隐藏
 * Dock 占着栈顶会挡住可见 Dock 的 Esc。
 */

import { Z_LAYERS } from '../../lib/zLayers'

// symbol 做实例身份：跨模块唯一、不可伪造。
const stack: symbol[] = []

// 轻量订阅（#801 codex 轮 2）：栈位映射视觉层级（z-index = 900 + 栈位），
// 实例经 useSyncExternalStore 跟随自己的栈位变化。栈变动是低频事件
// （mount/交互/关闭），listener 全量通知即可。
const listeners = new Set<() => void>()

function emitChange(): void {
  for (const listener of listeners) listener()
}

export function dockStackSubscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** 入栈/置顶（幂等——已存在先移除再压顶）。 */
export function dockStackRaise(id: symbol): void {
  const index = stack.indexOf(id)
  if (index !== -1) stack.splice(index, 1)
  stack.push(id)
  emitChange()
}

/** 出栈（unmount/关闭/hidden）。 */
export function dockStackRemove(id: symbol): void {
  const index = stack.indexOf(id)
  if (index === -1) return
  stack.splice(index, 1)
  emitChange()
}

/** 自己是否栈顶（Esc 消费门槛）。 */
export function dockStackIsTop(id: symbol): boolean {
  return stack[stack.length - 1] === id
}

/** 栈位映射 z-index：900 + 栈位，钳到 999——必须低于全局对话框与 Toast
 * （刻度见 lib/zLayers.ts），栈深超过 100 层时不再继续抬。 */
export function dockStackZIndex(id: symbol): number {
  const { dockBase: base, dockMax: max } = Z_LAYERS
  const index = stack.indexOf(id)
  if (index === -1) return base
  return Math.min(base + index, max)
}
