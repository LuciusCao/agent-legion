/**
 * 模块级抽屉栈（PR #812 codex P2）：Studio 右侧两个 persistent Drawer
 * （节点详情 + 共享素材）可同时打开，且各自在 document capture 阶段挂 Esc
 * 监听——没有栈的话先注册的监听器先 preventDefault，栈序与视觉层级脱钩
 * （先开的共享素材在下层却先吃掉 Esc）。实例打开入栈、关闭出栈，
 * `useDrawerEscape` 只在自己是栈顶（最近打开、视觉最上层）时消费 Esc。
 * 与 Dock 栈（features/agentPanelDock/dockStack.ts）同款结构，但抽屉不映射
 * z-index（persistent 的 paper 是 fixed 自定位、层级由 CSS/DOM 序决定），
 * 也不需要订阅——故只有 raise/remove/isTop 三个原语。
 */

// symbol 做实例身份：跨模块唯一、不可伪造。
const stack: symbol[] = []

/** 入栈/置顶（幂等——已存在先移除再压顶；重新打开语义上就是最前）。 */
export function drawerStackRaise(id: symbol): void {
  const index = stack.indexOf(id)
  if (index !== -1) stack.splice(index, 1)
  stack.push(id)
}

/** 出栈（关闭/unmount）。 */
export function drawerStackRemove(id: symbol): void {
  const index = stack.indexOf(id)
  if (index === -1) return
  stack.splice(index, 1)
}

/** 自己是否栈顶（Esc 消费门槛）。 */
export function drawerStackIsTop(id: symbol): boolean {
  return stack[stack.length - 1] === id
}
