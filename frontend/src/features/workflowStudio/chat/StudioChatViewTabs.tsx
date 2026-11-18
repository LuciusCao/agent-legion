import { useRef, type KeyboardEvent } from 'react'
import styles from './StudioChatView.module.css'

export type StudioChatView = 'chat' | 'tasks'

const VIEWS: { key: StudioChatView; label: string }[] = [
  { key: 'chat', label: '对话' },
  { key: 'tasks', label: '后台任务' },
]

export function StudioChatViewTabs({
  value,
  taskCount,
  baseId,
  onChange,
}: {
  value: StudioChatView
  taskCount: number
  baseId: string
  onChange: (view: StudioChatView) => void
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const current = VIEWS.findIndex((view) => view.key === value)
    let next: number
    if (event.key === 'ArrowLeft')
      next = (current - 1 + VIEWS.length) % VIEWS.length
    else if (event.key === 'ArrowRight') next = (current + 1) % VIEWS.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = VIEWS.length - 1
    else return
    event.preventDefault()
    onChange(VIEWS[next].key)
    refs.current[next]?.focus()
  }

  return (
    <div
      className={styles.tabs}
      role="tablist"
      aria-label="Agent 助手视图"
      onKeyDown={onKeyDown}
    >
      {VIEWS.map((view, index) => {
        const selected = value === view.key
        return (
          <button
            key={view.key}
            ref={(element) => {
              refs.current[index] = element
            }}
            id={`${baseId}-${view.key}-tab`}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={`${baseId}-${view.key}-panel`}
            tabIndex={selected ? 0 : -1}
            className={selected ? styles.activeTab : styles.tab}
            onClick={() => onChange(view.key)}
          >
            {view.label}
            {view.key === 'tasks' && (
              <span className={styles.taskCount} aria-hidden="true">
                {taskCount}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
