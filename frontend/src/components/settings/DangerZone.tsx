import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@mui/material'
import { useDeleteWorkspace } from '../../hooks/useWorkspaceMutations'
import DeleteWorkspaceDialog from '../DeleteWorkspaceDialog'
import styles from './DangerZone.module.css'

interface Props {
  workspaceId: string
  workspaceName: string
}

export function DangerZone({ workspaceId, workspaceName }: Props) {
  const navigate = useNavigate()
  const deleteWorkspace = useDeleteWorkspace()
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  async function handleConfirm() {
    await deleteWorkspace.mutateAsync(workspaceId)
    setDeleteDialogOpen(false)
    navigate('/')
  }

  return (
    <>
      <div className={styles.dangerRow}>
        <div>
          <div className={styles.dangerTitle}>删除 Workspace</div>
          <div className={styles.dangerDescription}>
            删除后不可恢复，相关任务记录将被移除，但磁盘产物文件不会自动清理。
          </div>
        </div>
        {workspaceId !== 'default' && (
          <Button
            variant="outlined"
            color="error"
            onClick={() => setDeleteDialogOpen(true)}
            disabled={workspaceName.trim() === ''}
          >
            删除 Workspace
          </Button>
        )}
      </div>
      <DeleteWorkspaceDialog
        open={deleteDialogOpen}
        workspaceName={workspaceName}
        workspaceId={workspaceId}
        onClose={() => setDeleteDialogOpen(false)}
        onConfirm={handleConfirm}
      />
    </>
  )
}
