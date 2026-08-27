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
import { useCreateWorkspace } from '../hooks/useWorkspaceMutations'

type Props = {
  open: boolean
  onClose: () => void
}

// schema v61：id 即 workflow key，创建后不可变，与后端
// WorkspaceCreateRequest 的 pattern 保持一致。
const WORKSPACE_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/

export default function CreateWorkspaceDialog({ open, onClose }: Props) {
  const createWorkspace = useCreateWorkspace()
  const [id, setId] = useState('')
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const idValid = WORKSPACE_ID_PATTERN.test(id)

  function handleClose() {
    setId('')
    setName('')
    setError(null)
    onClose()
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!idValid || !name.trim()) return
    setError(null)
    setCreating(true)
    try {
      await createWorkspace.mutateAsync({ id, name: name.trim() })
      handleClose()
    } catch (err) {
      setError(String(err))
    } finally {
      setCreating(false)
    }
  }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>{WORKSPACE_LABELS.createWorkspace}</DialogTitle>
      <DialogContent>
        <form
          onSubmit={handleSubmit}
          style={{ display: 'flex', flexDirection: 'column', gap: 16 }}
        >
          <TextField
            variant="outlined"
            label="Workspace ID"
            value={id}
            onChange={(e) => setId(e.target.value)}
            helperText="仅小写字母、数字、-、_，创建后不可修改（同时作为 Workflow Key）"
            error={id.length > 0 && !idValid}
            required
          />
          <TextField
            variant="outlined"
            label={WORKSPACE_LABELS.workspaceName}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          {error && (
            <div style={{ color: '#ba1a1a', fontSize: 12 }}>{error}</div>
          )}
        </form>
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={handleClose} disabled={creating}>
          取消
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={creating || !idValid || !name.trim()}
        >
          {creating ? '创建中…' : WORKSPACE_LABELS.create}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
