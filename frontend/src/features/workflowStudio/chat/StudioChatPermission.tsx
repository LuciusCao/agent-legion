import type { PermissionView } from './studioChatMessages'
import {
  permissionActions,
  permissionPresentation,
} from './studioChatPermissionPresentation'
import styles from './StudioChatPermission.module.css'

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
  const { pending, unbound, state, details, lateDetails, summary, ariaLabel } =
    permissionPresentation(permission)
  const actions = permissionActions(permission, unbound)
  return (
    <div
      className={`${styles.permission}${pending ? ` ${styles.permissionPending}` : ''}`}
      role={pending ? 'alert' : 'group'}
      aria-label="权限请求"
    >
      <div className={styles.permissionTitle}>
        {pending && (
          <span className={styles.permissionBadge}>需要你的确认</span>
        )}
        {state && <span className={styles.permissionBadge}>{state}</span>}
        Agent 请求权限：<code>{permission.toolTitle}</code>
      </div>
      {details && (
        <pre
          className={`${styles.permissionCommand}${summary ? ` ${styles.permissionSummary}` : ''}`}
          aria-label={ariaLabel}
        >
          {details}
        </pre>
      )}
      {lateDetails && (
        <pre
          className={styles.permissionCommand}
          aria-label="批准后取得 · 仅在授权围栏开启时绑定的完整命令"
        >
          {lateDetails}
        </pre>
      )}
      {permission.resolved ? (
        <div className={styles.permissionResolved}>
          {permission.decisionText ?? '已处理'}
          {state ? ` · ${state}` : ''}
        </div>
      ) : (
        <>
          <div className={styles.permissionActions}>
            {actions.map((action) => (
              <button
                key={action.optionId || action.kind}
                type="button"
                className={
                  action.deny ? styles.permissionDeny : styles.permissionAllow
                }
                disabled={props.disabled}
                onClick={() =>
                  props.onAnswer(
                    permission.requestId,
                    action.deny
                      ? { deny: true }
                      : { option_id: action.optionId }
                  )
                }
              >
                {action.name}
              </button>
            ))}
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
