import type { PermissionView } from './studioChatMessages'
import styles from './StudioChatPanel.module.css'

type Props = {
  permission: PermissionView
  allowAll: boolean
  disabled: boolean
  onAnswer: (
    requestId: string,
    answer: { option_id?: string; deny?: boolean }
  ) => void
  onToggleAllowAll: (enabled: boolean) => void
}

export function StudioChatPermission(props: Props) {
  const { permission } = props
  const allowOptions = permission.options.filter((option) =>
    option.kind.startsWith('allow')
  )
  const buttons = allowOptions.length > 0 ? allowOptions : permission.options
  return (
    <div className={styles.permission} role="group" aria-label="权限请求">
      <div className={styles.permissionTitle}>
        Agent 请求权限：<code>{permission.toolTitle}</code>
      </div>
      {permission.resolved ? (
        <div className={styles.permissionResolved}>
          {permission.decisionText ?? '已处理'}
        </div>
      ) : (
        <>
          <div className={styles.permissionActions}>
            {buttons.map((option) => (
              <button
                key={option.optionId}
                type="button"
                className={styles.permissionAllow}
                disabled={props.disabled}
                onClick={() =>
                  props.onAnswer(permission.requestId, {
                    option_id: option.optionId,
                  })
                }
              >
                {option.name}
              </button>
            ))}
            <button
              type="button"
              className={styles.permissionDeny}
              disabled={props.disabled}
              onClick={() =>
                props.onAnswer(permission.requestId, { deny: true })
              }
            >
              拒绝
            </button>
          </div>
          <label className={styles.allowAll}>
            <input
              type="checkbox"
              checked={props.allowAll}
              onChange={(event) => props.onToggleAllowAll(event.target.checked)}
            />
            本次对话全部允许（仍仅限草稿类操作）
          </label>
        </>
      )}
    </div>
  )
}
