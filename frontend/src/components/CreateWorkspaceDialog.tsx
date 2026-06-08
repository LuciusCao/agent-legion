import { useState } from 'react'
import { useWorkspaceStore } from '../stores/workspaceStore'

type Props = {
  open: boolean
  onClose: () => void
}

export default function CreateWorkspaceDialog({ open, onClose }: Props) {
  const { createWorkspace } = useWorkspaceStore()
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleClose() {
    setName('')
    setError(null)
    onClose()
  }

  if (!open) return null

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setError(null)
    setCreating(true)
    try {
      await createWorkspace(name.trim())
      handleClose()
    } catch (err) {
      setError(String(err))
    } finally {
      setCreating(false)
    }
  }

  return (
    <md-dialog open onClosed={handleClose}>
      <div slot="headline">新建 Workspace</div>
      <form
        slot="content"
        onSubmit={handleSubmit}
        style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 320 }}
      >
        <md-outlined-text-field
          label="Workspace 名称"
          value={name}
          onInput={(e: Event) => setName((e.target as HTMLInputElement).value)}
          required
        />
        {error && (
          <div style={{ color: 'var(--md-sys-color-error)', fontSize: 12 }}>{error}</div>
        )}
      </form>
      <div slot="actions">
        <md-text-button onClick={handleClose}>取消</md-text-button>
        <md-filled-button onClick={handleSubmit} disabled={creating}>
          {creating ? '创建中…' : '创建'}
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
