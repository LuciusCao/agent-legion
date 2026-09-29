/**
 * Esc 关闭的 document 级监听（codex P2 on #796/#797；#795 收尾起语义从
 * 「折叠」改为「关闭」——折叠态移除，Esc 与标题栏关闭按钮同一路径）：
 * 非模态面板失焦（用户点回底层页面）后 Esc 仍应关闭——挂在 document 而非
 * Paper。hidden 态不挂；同页多实例时只关栈顶（模块级 Dock 栈，见
 * dockStack.ts，#801 codex P1）；不抢已消费的 Esc：defaultPrevented 跳过；
 * IME 组字中的 Esc 是取消候选（复审批次 P3，与 composer Enter 的
 * isComposing 守卫同款）；有更上层的模态浮层开着时让给对方——判定不绑死
 * MUI：`.MuiModal-root`（ModalManager 体系，如 TokenUsage/菜单）或任何
 * aria-modal 对话框（复审批次 P2：DagFullscreenDialog 是全屏 role=dialog
 * aria-modal=true 的非 MUI 浮层，z 1000 > 900；ArtifactPopover 的 Esc 由它
 * 自己 capture 阶段消费，见 useArtifactPopover）。
 * 回调经 ref 读最新值（codex P2 复审轮）：effect 只按开关状态挂/卸，
 * 若闭包冻结首渲染的 onEscape，回调语义会过期——ref 保证每次击键读的是
 * 当帧回调。
 */
import { useEffect, useRef } from 'react'
import { dockStackIsTop, dockStackRaise, dockStackRemove } from './dockStack'

export function useDockEscape(
  suppressed: boolean,
  onEscape: () => void,
  stackId: symbol
): void {
  const onEscapeRef = useRef(onEscape)
  useEffect(() => {
    onEscapeRef.current = onEscape
  })
  // 栈成员资格随可见性走（hidden 不入栈，见 dockStack.ts）；重新可见时
  // 入栈即置顶（重新打开语义上就是最前）。
  useEffect(() => {
    if (suppressed) return
    dockStackRaise(stackId)
    return () => dockStackRemove(stackId)
  }, [suppressed, stackId])
  useEffect(() => {
    if (suppressed) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      if (event.isComposing) return
      // 多实例共存时只关栈顶（#801 codex P1）：不是栈顶的 Dock 不动。
      if (!dockStackIsTop(stackId)) return
      if (
        document.querySelector(
          '.MuiModal-root, [role="dialog"][aria-modal="true"]'
        )
      )
        return
      onEscapeRef.current()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [suppressed, stackId])
}
