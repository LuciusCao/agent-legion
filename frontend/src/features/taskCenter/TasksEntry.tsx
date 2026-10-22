import { useLocation, useNavigate } from 'react-router-dom'
import { LabeledIconButton } from '../../components/LabeledIconButton'
import { useActiveRunBatches } from './useRunBatches'
import { useActiveJobBatchOps } from './useJobBatchOpListing'
import chromeStyles from './TaskCenterChrome.module.css'

/** 任务中心 AppBar 入口（#1157 phase3）：徽标 = 进行中 + 排队（纯数字，
 * 无动效——用户验收裁决：有个数字就行了）。#946 M52：批量创建
 * （run_batches）与批量操作（job_batch_operations）的 active 计数合并。 */
export function TasksEntry({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate()
  const location = useLocation()
  const activeBatches = useActiveRunBatches(workspaceId)
  const activeOps = useActiveJobBatchOps(workspaceId)
  const count =
    (activeBatches.data?.active_count ?? 0) +
    (activeOps.data?.active_count ?? 0)
  const onTasksPage = location.pathname.endsWith('/tasks')
  return (
    <span className={chromeStyles.badgeWrap}>
      <LabeledIconButton
        icon="inventory_2"
        label="任务"
        active={onTasksPage}
        ariaLabel="任务中心"
        tooltip="批量操作进度（任务中心）"
        onClick={() => navigate(`/workspaces/${workspaceId}/tasks`)}
      />
      {count > 0 && (
        <span className={chromeStyles.badge} data-testid="tasks-badge">
          {count}
        </span>
      )}
    </span>
  )
}
