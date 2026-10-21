import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTaskCenterStore } from '../../stores/taskCenterStore'
import styles from './TaskCenterChrome.module.css'

const AUTO_DISMISS_MS = 8000

/**
 * 大批量异步受理通知（#1157 phase3）：关对话框后的秒级回执——持久
 * snackbar（8s），带「查看进度 →」跳任务中心；AppBar 徽标数字由
 * active 查询轮询即时刷新（纯数字，无动效）。
 */
export function TaskAcceptToast({ workspaceId }: { workspaceId?: string }) {
  const notice = useTaskCenterStore((s) => s.acceptedNotice)
  const clear = useTaskCenterStore((s) => s.clearAcceptedNotice)
  const navigate = useNavigate()
  const visible = Boolean(notice && notice.workspaceId === workspaceId)

  // 定时器挂 notice 存在性（reviewer r1 P2-3）：8s 内切到别的 workspace
  // 不再取消计时——回到本 workspace 时过期通知已被清掉，不会 stale 复现。
  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(clear, AUTO_DISMISS_MS)
    return () => window.clearTimeout(timer)
  }, [notice, clear])

  if (!visible || !notice) return null
  return (
    <div
      className={styles.acceptToast}
      role="status"
      data-testid="task-accept-toast"
    >
      <span>
        已受理 {notice.itemsTotal.toLocaleString()} 个条目，正在后台创建。
        <br />
        进度见右上角「任务」，期间可以继续其他操作。
      </span>
      <button
        className={styles.goto}
        onClick={() => {
          clear()
          navigate(`/workspaces/${notice.workspaceId}/tasks`)
        }}
      >
        查看进度 →
      </button>
    </div>
  )
}
