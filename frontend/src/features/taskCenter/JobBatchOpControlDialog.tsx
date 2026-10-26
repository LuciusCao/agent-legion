import { ConfirmDialog } from '../../components/ConfirmDialog'
import styles from './TaskCenterPage.module.css'
import type { JobBatchOpPendingControl } from './useJobBatchOpActions'
import { useJobBatchOpActions } from './useJobBatchOpActions'

export function JobBatchOpControlDialog({
  workspaceId,
  control,
  onClose,
}: {
  workspaceId: string | undefined
  control: JobBatchOpPendingControl | null
  onClose: () => void
}) {
  const opActions = useJobBatchOpActions(workspaceId)
  const paused = control?.action === 'pause'
  const remaining = control
    ? Math.max(0, control.card.total - control.card.processed)
    : 0

  return (
    <ConfirmDialog
      open={control !== null}
      title={`${paused ? '暂停' : '取消'}${control?.card.title ?? ''}？`}
      confirmLabel={paused ? '确认暂停' : '确认取消'}
      confirmColor={paused ? 'primary' : 'error'}
      busyLabel={paused ? '暂停中…' : '取消中…'}
      onClose={onClose}
      onConfirm={async () => {
        if (!control) return
        try {
          await opActions[control.action].mutateAsync(control.card.id)
        } finally {
          onClose()
        }
      }}
    >
      {control && (
        <div>
          <p>
            操作 <span className={styles.mono}>{control.card.refId}</span> · 共{' '}
            {control.card.total.toLocaleString()} 条
          </p>
          {paused ? (
            <>
              <p>
                暂停后不再处理剩余 {remaining.toLocaleString()}{' '}
                条；已开始处理的条目可能仍会完成。
              </p>
              <p>已处理结果保留，之后可以点击「继续」恢复。</p>
            </>
          ) : (
            <>
              <p>取消后不可恢复。</p>
              <p>
                将停止处理剩余 {remaining.toLocaleString()} 条；取消前已处理的{' '}
                {control.card.processed.toLocaleString()}{' '}
                条结果保留，不会撤销或回滚。
              </p>
              <p>已开始处理的条目可能仍会完成。</p>
              {control.card.title === '批量通过' && (
                <p>已通过的审批不会撤销。</p>
              )}
            </>
          )}
        </div>
      )}
    </ConfirmDialog>
  )
}
