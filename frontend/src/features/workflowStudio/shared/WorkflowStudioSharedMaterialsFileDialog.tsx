import { useQuery } from '@tanstack/react-query'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { getWorkspaceSharedMaterialFile } from '../../../api'
import styles from './WorkflowStudioSharedMaterialsFileDialog.module.css'

/** 单个共享文件的只读文本查看 Dialog（按需查询，等宽展示，超上限提示截断）。 */
export function SharedMaterialFileContentDialog({
  workspaceId,
  path,
  onClose,
}: {
  workspaceId: string
  path: string
  onClose: () => void
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['workspaceSharedMaterialFile', workspaceId, path],
    queryFn: () => getWorkspaceSharedMaterialFile(workspaceId, path),
  })
  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <code className={styles.dialogPath}>{path}</code>
      </DialogTitle>
      <DialogContent>
        {error ? (
          <p className={styles.error} role="alert">
            {(error as Error).message}
          </p>
        ) : isLoading ? (
          <p className={styles.empty}>加载中…</p>
        ) : (
          <>
            {data?.truncated && (
              <p className={styles.hint}>文件超过大小上限，仅显示前 128 KB。</p>
            )}
            <pre className={styles.fileContent}>{data?.content ?? ''}</pre>
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose}>
          关闭
        </Button>
      </DialogActions>
    </Dialog>
  )
}
