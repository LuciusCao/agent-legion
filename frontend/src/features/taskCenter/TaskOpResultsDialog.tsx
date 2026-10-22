import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { useJobBatchOpResults } from './useJobBatchOps'
import { jobBatchOpReasonLabel } from './jobBatchOpMeta'
import styles from './TaskOpResultsDialog.module.css'

const RESULT_STATUS_LABEL: Record<string, string> = {
  succeeded: '成功',
  skipped: '跳过',
  failed: '失败',
}

/**
 * 批量操作（#946）逐 job 结果查看：keyset 分页瀑布流（「加载更多」），
 * 每行 = job 短 id + 结果状态 + 原因标签 + 服务端 message。
 */
export function TaskOpResultsDialog({
  workspaceId,
  opId,
  title,
  open,
  onClose,
}: {
  workspaceId: string
  opId: string | null
  title: string
  open: boolean
  onClose: () => void
}) {
  const query = useJobBatchOpResults(workspaceId, opId ?? undefined, open)
  const items = (query.data?.pages ?? []).flatMap((page) => page.results)
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{title} · 逐条结果</DialogTitle>
      <DialogContent dividers>
        {query.isLoading && <div className={styles.hint}>加载中…</div>}
        {query.isError && (
          <div className={styles.hint}>
            加载失败：
            {query.error instanceof Error
              ? query.error.message
              : String(query.error)}
          </div>
        )}
        {!query.isLoading && !query.isError && items.length === 0 && (
          <div className={styles.hint}>没有逐条结果记录。</div>
        )}
        {items.length > 0 && (
          <table className={styles.resultTable}>
            <thead>
              <tr>
                <th>#</th>
                <th>任务</th>
                <th>结果</th>
                <th>原因 / 说明</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const result = item.result as {
                  job_id?: string
                  status?: string
                  reason_code?: string | null
                  message?: string | null
                }
                return (
                  <tr key={item.seq}>
                    <td className={styles.mono}>{item.seq}</td>
                    <td
                      className={styles.mono}
                      title={String(result.job_id ?? '')}
                    >
                      {String(result.job_id ?? '').slice(0, 12) || '—'}
                    </td>
                    <td>
                      <span
                        className={`${styles.resultStatus} ${
                          styles[result.status ?? ''] ?? ''
                        }`}
                      >
                        {RESULT_STATUS_LABEL[result.status ?? ''] ??
                          result.status ??
                          '—'}
                      </span>
                    </td>
                    <td className={styles.reasonCell}>
                      {result.reason_code
                        ? jobBatchOpReasonLabel(result.reason_code)
                        : ''}
                      {result.message ? (
                        <span className={styles.message}>{result.message}</span>
                      ) : null}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        {query.hasNextPage && (
          <div className={styles.moreWrap}>
            <Button
              size="small"
              disabled={query.isFetchingNextPage}
              onClick={() => query.fetchNextPage()}
            >
              {query.isFetchingNextPage ? '加载中…' : '加载更多'}
            </Button>
          </div>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>关闭</Button>
      </DialogActions>
    </Dialog>
  )
}
