import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@mui/material'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import DeleteWorkspaceDialog from '../DeleteWorkspaceDialog'
import styles from './DangerZone.module.css'

interface Props {
  workspaceId: string
  workspaceName: string
}

export function DangerZone({ workspaceId, workspaceName }: Props) {
  const navigate = useNavigate()
  const { deleteWorkspace } = useWorkspaceStore()
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  async function handleConfirm() {
    await deleteWorkspace(workspaceId)
    setDeleteDialogOpen(false)
    navigate('/')
  }

  return (
    <>
      <section className={styles.dangerZone}>
        <h2 className={styles.sectionTitle}>危险操作</h2>
        <hr className={styles.sectionDivider} />
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
            >
              删除 Workspace
            </Button>
          )}
        </div>
      </section>
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
