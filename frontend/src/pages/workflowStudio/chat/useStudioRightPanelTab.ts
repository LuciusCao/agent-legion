import { useState } from 'react'
import type { StudioRightPanelTab } from './StudioRightPanelTabs'

/** 右栏 tab 状态：chat tab 选中时侧栏即使无选中节点也保持打开；
 * 选中节点自动切回节点配置 tab（保持既有交互），关闭面板复位到 inspector。 */
export function useStudioRightPanelTab(
  setSelectedNodeKey: (nodeKey: string | null) => void
) {
  const [tab, setTab] = useState<StudioRightPanelTab>('inspector')
  return {
    tab,
    setTab,
    chatOpen: tab === 'chat',
    selectNode: (nodeKey: string | null) => {
      setSelectedNodeKey(nodeKey)
      if (nodeKey !== null) setTab('inspector')
    },
    closePanel: () => {
      setSelectedNodeKey(null)
      setTab('inspector')
    },
  }
}
