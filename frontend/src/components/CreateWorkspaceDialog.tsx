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
import { BlankWorkflowCheckbox } from './settings/BlankWorkflowCheckbox'
import { WorkflowSection } from './settings/WorkflowSection'

type Props = {
  open: boolean
  onClose: () => void
}

export default function CreateWorkspaceDialog({ open, onClose }: Props) {
  const createWorkspace = useCreateWorkspace()
  const [name, setName] = useState('')
  const [workflowKey, setWorkflowKey] = useState('')
  const [blankMode, setBlankMode] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleClose() {
    setName('')
    setWorkflowKey('')
    setBlankMode(false)
    setError(null)
    onClose()
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim() || !workflowKey) return
    setError(null)
    setCreating(true)
    try {
      await createWorkspace.mutateAsync({
        name: name.trim(),
        workflowKey,
        workflowMode: blankMode ? 'blank' : 'demo',
      })
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
            label={WORKSPACE_LABELS.workspaceName}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <WorkflowSection
            workflowKey={workflowKey}
            onChange={setWorkflowKey}
          />
          <BlankWorkflowCheckbox checked={blankMode} onChange={setBlankMode} />
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
          disabled={creating || !name.trim() || !workflowKey}
        >
          {creating ? '创建中…' : WORKSPACE_LABELS.create}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
