import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchWorkflowRuntimeVersions } from '../../../api'
import { useStudioStateOptional } from '../shared/studioStateContext'
import { formatRuntimeChangedKeys } from '../shared/workflowRuntimeSlice'
import styles from './WorkflowRuntimeVersionsDialog.module.css'

function formatTime(value: string): string {
  const at = new Date(value)
  return Number.isNaN(at.getTime())
    ? value
    : at.toLocaleString('zh-CN', { hour12: false })
}

/**
 * M48 运行配置版本历史（M47 端点）：每次运行配置保存/回滚追加一条不可变
 * 版本行。回滚 = 把目标版本的 runtime 切片应用到当前结构后追加为新版本，
 * 历史永不篡改。只读 revision 视图下可查看但不可回滚（回滚作用于 active，
 * 回到草稿后操作语义更清楚）。
 */
export function WorkflowRuntimeVersionsDialog(props: {
  open: boolean
  workspaceId: string
  readOnly: boolean
  onClose: () => void
}) {
  const studio = useStudioStateOptional()
  const queryClient = useQueryClient()
  const versionsQuery = useQuery({
    queryKey: ['workflowRuntimeVersions', props.workspaceId],
    queryFn: () => fetchWorkflowRuntimeVersions(props.workspaceId),
    enabled: props.open,
  })
  const versions = [...(versionsQuery.data?.versions ?? [])].sort(
    (a, b) => b.version - a.version
  )

  const rollback = async (version: number) => {
    if (!studio) return
    const ok = await studio.rollbackRuntimeConfig(version)
    if (ok) {
      await queryClient.invalidateQueries({
        queryKey: ['workflowRuntimeVersions', props.workspaceId],
      })
      props.onClose()
    }
  }

  return (
    <Dialog open={props.open} onClose={props.onClose} maxWidth="sm" fullWidth>
      <DialogTitle>运行配置版本历史</DialogTitle>
      <DialogContent dividers>
        {versionsQuery.isPending && <p className={styles.meta}>加载中…</p>}
        {versionsQuery.isError && (
          <p className={styles.meta} role="alert">
            版本历史加载失败，请关闭后重试。
          </p>
        )}
        {versionsQuery.isSuccess && versions.length === 0 && (
          <p className={styles.meta}>
            还没有运行配置版本——首次在「运行配置」区保存后生成。
          </p>
        )}
        {versions.map((version) => (
          <div key={version.version} className={styles.row}>
            <div className={styles.rowMain}>
              <span className={styles.versionTag}>v{version.version}</span>
              <span className={styles.time}>
                {formatTime(version.created_at)}
              </span>
              <span
                className={styles.keys}
                title={version.changed_keys.join(', ')}
              >
                {formatRuntimeChangedKeys(version.changed_keys)}
              </span>
              <span className={styles.meta}>{version.created_by}</span>
            </div>
            <Button
              size="small"
              variant="outlined"
              disabled={props.readOnly || studio?.runtimeSaving || !studio}
              title={
                props.readOnly ? '查看历史版本时不可回滚，请回到草稿后操作' : ''
              }
              onClick={() => void rollback(version.version)}
            >
              回滚到此版本
            </Button>
          </div>
        ))}
        {versions.length > 0 && (
          <p className={styles.meta}>
            回滚立即生效并追加为新版本；历史记录不会被修改。
          </p>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={props.onClose}>关闭</Button>
      </DialogActions>
    </Dialog>
  )
}
