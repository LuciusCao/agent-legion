import { useEffect, useId, useMemo, useState } from 'react'
import {
  ExpandMoreOutlined,
  HelpOutlineOutlined,
  SmartToyOutlined,
  TerminalOutlined,
} from '@mui/icons-material'
import { formatDateTime } from '../../../lib/formatters'
import {
  backgroundTaskElapsedAt,
  backgroundTaskElapsedText,
  backgroundTaskStatusLabel,
  type StudioBackgroundTask,
  type StudioBackgroundTaskKind,
} from './studioBackgroundTaskModel'
import styles from './StudioChatView.module.css'

type TaskFilter = 'all' | Exclude<StudioBackgroundTaskKind, 'unknown'>

function useTaskClock(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  return now
}

function KindIcon({ kind }: { kind: StudioBackgroundTaskKind }) {
  if (kind === 'agent') return <SmartToyOutlined aria-hidden="true" />
  if (kind === 'bash') return <TerminalOutlined aria-hidden="true" />
  return <HelpOutlineOutlined aria-hidden="true" />
}

function TaskCard({
  task,
  expanded,
  now,
  onToggle,
}: {
  task: StudioBackgroundTask
  expanded: boolean
  now: number
  onToggle: () => void
}) {
  const baseId = useId()
  const detailId = `${baseId}-detail`
  const triggerId = `${baseId}-trigger`
  const elapsed = backgroundTaskElapsedText(backgroundTaskElapsedAt(task, now))
  const subline = task.terminal ? task.summary || task.detail : task.detail
  return (
    <article className={styles.taskCard} data-status={task.status}>
      <button
        id={triggerId}
        type="button"
        className={styles.taskTrigger}
        aria-expanded={expanded}
        aria-controls={detailId}
        onClick={onToggle}
      >
        <span className={styles.kindIcon} data-kind={task.kind}>
          <KindIcon kind={task.kind} />
        </span>
        <span className={styles.taskMain}>
          <span className={styles.taskTitleRow}>
            <span className={styles.taskTitle} title={task.description}>
              {task.description}
            </span>
            <code className={styles.taskId}>{task.taskId}</code>
          </span>
          <span className={styles.taskMeta}>
            <span>
              {task.kind === 'unknown'
                ? '未知类型'
                : task.kind === 'agent'
                  ? 'Agent'
                  : 'Bash'}
            </span>
            <span className={styles.taskStatus} data-status={task.status}>
              {backgroundTaskStatusLabel(task.status)}
            </span>
            <span className={styles.taskElapsed}>耗时 {elapsed}</span>
          </span>
          {subline && <span className={styles.taskSubline}>{subline}</span>}
        </span>
        <ExpandMoreOutlined
          className={styles.expandIcon}
          data-expanded={expanded}
          aria-hidden="true"
        />
      </button>
      <div
        id={detailId}
        className={styles.taskDetail}
        role="region"
        aria-labelledby={triggerId}
        hidden={!expanded}
      >
        {task.terminal && (
          <>
            <h3 className={styles.detailHeading}>终态摘要</h3>
            <p className={styles.detailSummary}>
              {task.summary || '暂无输出摘要，可让助手核对结果'}
            </p>
          </>
        )}
        <h3 className={styles.detailHeading}>事件历史</h3>
        <ol className={styles.taskHistory}>
          {task.history.map((item) => (
            <li key={item.messageId}>
              <div className={styles.historyMeta}>
                <strong>{backgroundTaskStatusLabel(item.status)}</strong>
                <time dateTime={item.createdAt}>
                  {formatDateTime(item.createdAt)}
                </time>
              </div>
              {item.detail && <p>{item.detail}</p>}
            </li>
          ))}
        </ol>
      </div>
    </article>
  )
}

export function StudioChatBackgroundTasks({
  id,
  labelledBy,
  tasks,
  hidden,
}: {
  id: string
  labelledBy: string
  tasks: StudioBackgroundTask[]
  hidden: boolean
}) {
  const [filter, setFilter] = useState<TaskFilter>('all')
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const now = useTaskClock(tasks.some((task) => !task.terminal))
  const visibleTasks = useMemo(
    () =>
      filter === 'all' ? tasks : tasks.filter((task) => task.kind === filter),
    [tasks, filter]
  )

  return (
    <section
      id={id}
      className={styles.tasksPanel}
      role="tabpanel"
      aria-labelledby={labelledBy}
      hidden={hidden}
    >
      <div className={styles.tasksSummary}>
        <div>
          <h2>后台任务</h2>
          <p>中间进展在这里更新；对话仅保留首次观察与终态通知。</p>
        </div>
        <select
          aria-label="后台任务类型"
          className={styles.taskFilter}
          value={filter}
          onChange={(event) => setFilter(event.target.value as TaskFilter)}
        >
          <option value="all">全部类型</option>
          <option value="agent">Agent</option>
          <option value="bash">Bash</option>
        </select>
      </div>
      <div className={styles.taskList} aria-label="后台任务列表">
        {visibleTasks.length === 0 ? (
          <div className={styles.tasksEmpty}>
            {tasks.length === 0
              ? '当前会话还没有后台任务'
              : '没有符合筛选条件的后台任务'}
          </div>
        ) : (
          visibleTasks.map((task) => (
            <TaskCard
              key={task.taskId}
              task={task}
              now={now}
              expanded={expanded.has(task.taskId)}
              onToggle={() =>
                setExpanded((current) => {
                  const next = new Set(current)
                  if (next.has(task.taskId)) next.delete(task.taskId)
                  else next.add(task.taskId)
                  return next
                })
              }
            />
          ))
        )}
      </div>
    </section>
  )
}
