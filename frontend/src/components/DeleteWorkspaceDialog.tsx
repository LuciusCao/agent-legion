import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
} from '@mui/material'
import { WORKSPACE_LABELS } from '../labels'

type Props = {
  open: boolean
  workspaceName: string
  workspaceId: string
  onClose: () => void
  onConfirm: () => Promise<void>
}

export default function DeleteWorkspaceDialog({
  open,
  workspaceName,
  onClose,
  onConfirm,
}: Props) {
  const [inputValue, setInputValue] = useState('')
  const [isDeleting, setIsDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleClose() {
    setInputValue('')
    setError(null)
    setIsDeleting(false)
    onClose()
  }

  const confirmed =
    workspaceName.trim() !== '' && inputValue.trim() === workspaceName.trim()

  async function handleConfirm() {
    if (!confirmed) return
    setError(null)
    setIsDeleting(true)
    try {
      await onConfirm()
      handleClose()
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : '删除失败，请稍后重试'
      setError(message)
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <Dialog open={open} onClose={handleClose}>
      <DialogTitle>{WORKSPACE_LABELS.deleteWorkspace}</DialogTitle>
      <DialogContent>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
            minWidth: 320,
          }}
        >
          <div
            style={{
              padding: 12,
              borderRadius: 8,
              background: '#fee2e2',
              color: '#b91c1c',
              fontSize: 14,
              lineHeight: 1.6,
            }}
          >
            <strong>此操作不可撤销。</strong>
            <br />
            删除后，Workspace「{workspaceName}」及其所有任务记录将被永久移除，
            但磁盘上的产物文件不会自动清理。
          </div>
          <p
            style={{
              margin: 0,
              fontSize: 14,
              color: '#43474e',
            }}
          >
            请输入 Workspace 名称「<strong>{workspaceName}</strong>
            」以确认删除。
          </p>
          <TextField
            variant="outlined"
            label={WORKSPACE_LABELS.workspaceName}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onInput={(e) => setInputValue((e.target as HTMLInputElement).value)}
            disabled={isDeleting}
            fullWidth
          />
          {error && (
            <div style={{ color: '#ba1a1a', fontSize: 13 }}>{error}</div>
          )}
        </div>
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={handleClose} disabled={isDeleting}>
          取消
        </Button>
        <Button
          variant="contained"
          color="error"
          onClick={handleConfirm}
          disabled={!confirmed || isDeleting}
        >
          {isDeleting ? '删除中…' : WORKSPACE_LABELS.confirmDelete}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
