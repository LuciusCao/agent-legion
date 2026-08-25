import { useState } from 'react'
import { Alert } from '@mui/material'
import { useSettingStore } from '../../stores/settingStore'
import { EMPTY_WORKFLOW_GUIDANCE } from './workflowStudioEmptyState'
import styles from './WorkflowStudioEmptyGuide.module.css'

const dismissedKey = (workspaceId: string) =>
  `agent-legion:studio-empty-guide-dismissed:${workspaceId}`

function readDismissed(workspaceId: string): boolean {
  try {
    return globalThis.localStorage?.getItem(dismissedKey(workspaceId)) === '1'
  } catch {
    return false
  }
}

/** 空态（从未发布）引导横幅：MUI Alert，可关闭；关闭状态按 workspace 持久化。 */
export function WorkflowStudioEmptyGuide() {
  const workspaceId = useSettingStore((s) => s.workspaceId)
  const [dismissedFor, setDismissedFor] = useState<string | null>(null)
  if (!workspaceId) return null
  // 用「记录被关闭的 workspaceId」而非布尔 state：切换 workspace 时无需
  // effect 重置，每个 workspace 的关闭状态独立判断。
  if (dismissedFor === workspaceId || readDismissed(workspaceId)) return null
  return (
    <Alert
      severity="info"
      className={styles.guide}
      onClose={() => {
        try {
          globalThis.localStorage?.setItem(dismissedKey(workspaceId), '1')
        } catch {
          // localStorage 不可用时仅本次会话内关闭
        }
        setDismissedFor(workspaceId)
      }}
    >
      {EMPTY_WORKFLOW_GUIDANCE}
    </Alert>
  )
}
