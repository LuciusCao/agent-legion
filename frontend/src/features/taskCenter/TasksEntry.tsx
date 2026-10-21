import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { LabeledIconButton } from '../../components/LabeledIconButton'
import { useActiveRunBatches } from './useRunBatches'
import chromeStyles from './TaskCenterChrome.module.css'

/** 任务中心 AppBar 入口（#1157 phase3）：徽标 = 进行中 + 排队；计数上行
 * 时 pulse 三次（大批量异步受理的秒级视觉回执之一）。 */
export function TasksEntry({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate()
  const location = useLocation()
  const activeQuery = useActiveRunBatches(workspaceId)
  const count = activeQuery.data?.active_count ?? 0
  const [pulse, setPulse] = useState(false)
  const previousCount = useRef(count)
  useEffect(() => {
    if (count > previousCount.current) {
      setPulse(true)
      const timer = window.setTimeout(() => setPulse(false), 3000)
      previousCount.current = count
      return () => window.clearTimeout(timer)
    }
    previousCount.current = count
  }, [count])
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
        <span
          className={`${chromeStyles.badge} ${pulse ? chromeStyles.pulse : ''}`}
          data-testid="tasks-badge"
        >
          {count}
        </span>
      )}
    </span>
  )
}
