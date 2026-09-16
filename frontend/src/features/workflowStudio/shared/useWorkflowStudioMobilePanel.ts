import { useEffect, useState } from 'react'
import type { StudioMobilePanel } from './WorkflowStudioMobileNav'

/* focusNonce（useStudioNodeSelection 的定位请求信号）进依赖：目标节点已是
 * selectedNodeKey 时选中值不变，仅按 key 变化切换会漏掉「移动端在 Agent
 * 面板再次点同一节点」的场景——nonce 变化时同样切到编辑面板。 */
export function useWorkflowStudioMobilePanel(
  selectedNodeKey: string | null,
  focusNonce = 0
): {
  mobilePanel: StudioMobilePanel
  setMobilePanel: (value: StudioMobilePanel) => void
} {
  const [mobilePanel, setMobilePanel] = useState<StudioMobilePanel>('graph')

  useEffect(() => {
    if (selectedNodeKey) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setMobilePanel('editor')
    } else {
      setMobilePanel('graph')
    }
  }, [selectedNodeKey, focusNonce])

  return { mobilePanel, setMobilePanel }
}
