/**
 * Esc 关闭抽屉（persistent variant 不走 Modal，Esc 语义自行承接）：
 * capture 阶段监听 + preventDefault——Dock 的 Esc 处理器
 * （useDockEscape）见到 defaultPrevented 跳过，抽屉是更上层先消费。
 * IME 组字中的 Esc 是取消候选（不关闭）。
 * 多抽屉共存时只关栈顶（模块级抽屉栈，见 drawerStack.ts，PR #812 codex
 * P2）：打开入栈/关闭出栈，非栈顶的抽屉不动——否则先打开的抽屉（视觉
 * 下层）会抢先 preventDefault 并关掉自己，留下上层的节点详情抽屉。
 * 回调经 ref 读最新值（同 useDockEscape）：effect 只按开关状态挂/卸，
 * ref 保证每次击键读的是当帧回调。
 */
import { useEffect, useMemo, useRef } from 'react'
import {
  drawerStackIsTop,
  drawerStackRaise,
  drawerStackRemove,
} from './drawerStack'

export function useDrawerEscape(open: boolean, onClose: () => void) {
  // 实例身份随组件生命周期稳定（同一抽屉开关复用同一栈位）。
  const stackId = useMemo(() => Symbol('studio-drawer'), [])
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })
  // 栈成员资格随开合走：打开即置顶（重新打开语义上就是最前）。
  useEffect(() => {
    if (!open) return
    drawerStackRaise(stackId)
    return () => drawerStackRemove(stackId)
  }, [open, stackId])
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing || event.defaultPrevented)
        return
      // 多抽屉共存时只关栈顶（PR #812 codex P2）：不是栈顶的抽屉不动。
      if (!drawerStackIsTop(stackId)) return
      // hotfix 轮 2 codex P2：有更上层浮层在场即让位（capture 早于 MUI
      // Modal 的按键处理，不探测会把抽屉连同模态一起关掉）。与
      // useDockEscape 同款判定；Drawer persistent 不是 Modal，不会误伤
      // 自己。
      if (
        document.querySelector(
          '.MuiModal-root, .MuiPopover-root, [role="dialog"][aria-modal="true"]'
        )
      )
        return
      event.preventDefault()
      onCloseRef.current()
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [open, stackId])
}
