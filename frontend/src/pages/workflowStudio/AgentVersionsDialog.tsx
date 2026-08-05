import { useState } from 'react'
import {
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { fetchAgentVersions, rollbackAgent } from '../../api'
import type { AgentVersionSummary } from '../../types'
import { useAsync } from '../../hooks/useAsync'
import styles from './AgentsPanel.module.css'

const statusLabels: Record<AgentVersionSummary['status'], string> = {
  draft: '草稿',
  published: '已发布',
  archived: '已归档',
}

type Props = {
  agentId: string
  open: boolean
  onClose: () => void
  onRolledBack: () => void
}

/** Agent 版本历史：列出所有版本，回滚会以目标版本生成新草稿。 */
export function AgentVersionsDialog({
  agentId,
  open,
  onClose,
  onRolledBack,
}: Props) {
  const { data, loading, error } = useAsync(
    () => fetchAgentVersions(agentId),
    [agentId, open],
    { enabled: open }
  )
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const versions = data?.versions ?? []

  async function handleRollback(version: number) {
    if (!window.confirm(`确定要回滚到 v${version} 吗？将生成新草稿。`)) return
    setActionError('')
    setBusy(true)
    try {
      await rollbackAgent(agentId, version)
      onRolledBack()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '回滚失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>版本历史 · {agentId}</DialogTitle>
      <DialogContent dividers>
        {(error || actionError) && (
          <p className={styles.error} role="alert">
            {error || actionError}
          </p>
        )}
        {loading && <p className={styles.hint}>加载中...</p>}
        {!loading && versions.length === 0 && !error && (
          <p className={styles.empty}>暂无版本</p>
        )}
        <ul className={styles.listItems}>
          {versions.map((version) => (
            <li key={version.id} className={styles.listItemMeta}>
              <Chip size="small" label={`v${version.version}`} />
              <Chip size="small" label={statusLabels[version.status]} />
              <span>{new Date(version.created_at).toLocaleString()}</span>
              <span>{version.created_by}</span>
              {version.status !== 'draft' && (
                <Button
                  size="small"
                  disabled={busy}
                  onClick={() => void handleRollback(version.version)}
                >
                  回滚
                </Button>
              )}
            </li>
          ))}
        </ul>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>关闭</Button>
      </DialogActions>
    </Dialog>
  )
}
