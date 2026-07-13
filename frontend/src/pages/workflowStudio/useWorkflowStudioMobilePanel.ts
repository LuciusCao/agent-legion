import { useEffect, useState } from 'react'
import type { StudioMobilePanel } from './WorkflowStudioMobileNav'

export function useWorkflowStudioMobilePanel(selectedNodeKey: string | null): {
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
  }, [selectedNodeKey])

  return { mobilePanel, setMobilePanel }
}
