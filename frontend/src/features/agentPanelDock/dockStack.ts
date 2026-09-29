/**
 * 模块级 Dock 栈（#801 codex P1）：同页可同时开多个 Dock（如 job detail 的
 * 定制预览 + 排查），每个实例都在 document 挂 Esc 监听——没有栈的话一次
 * Esc 关掉全部（丢未发送文本、定制预览草稿授权失效）。实例 mount 入栈、
 * unmount/关闭出栈，pointerdown/focusin 交互时置顶；`useDockEscape` 只在
 * 自己是栈顶时消费 Esc。栈判定在事件发生瞬间现读（纯数据，无需订阅）。
 * hidden（suppressed）的实例不入栈：隐藏 Dock 占着栈顶会挡住可见 Dock
 * 的 Esc。
 */

// symbol 做实例身份：跨模块唯一、不可伪造。
const stack: symbol[] = []

/** 入栈/置顶（幂等——已存在先移除再压顶）。 */
export function dockStackRaise(id: symbol): void {
  const index = stack.indexOf(id)
  if (index !== -1) stack.splice(index, 1)
  stack.push(id)
}

/** 出栈（unmount/关闭/hidden）。 */
export function dockStackRemove(id: symbol): void {
  const index = stack.indexOf(id)
  if (index !== -1) stack.splice(index, 1)
}

/** 自己是否栈顶（Esc 消费门槛）。 */
export function dockStackIsTop(id: symbol): boolean {
  return stack[stack.length - 1] === id
}
