import { useNavigate, useParams } from 'react-router-dom'
import { MaterialIcon } from './MaterialIcon'
import { LabeledIconButton } from './LabeledIconButton'
import { useAuthStore } from '../stores/authStore'
import { useUiStore } from '../stores/uiStore'
import styles from './WorkspaceMoreMenu.module.css'

/**
 * 顶栏「更多」菜单：收纳低频的管理/分析入口（打包、用量、质量、Studio、设置），
 * 让常驻工具条只保留高频操作。Studio 入口 admin-only（与路由侧自守卫同一惯例，P4）。
 * hover 或聚焦按钮即展开面板（纯 CSS，与运行控件 popover 同一模式）。
 */
export function WorkspaceMoreMenu() {
  const navigate = useNavigate()
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const setWorkspacePackageDialogOpen = useUiStore(
    (s) => s.setWorkspacePackageDialogOpen
  )

  const go = (to: string) => () => navigate(to)

  const item = (
    icon: string,
    label: string,
    ariaLabel: string,
    onClick: () => void
  ) => (
    <button
      type="button"
      role="menuitem"
      aria-label={ariaLabel}
      className={styles.item}
      onClick={onClick}
    >
      <MaterialIcon name={icon} sx={{ fontSize: 20 }} />
      {label}
    </button>
  )

  return (
    <div className={styles.root}>
      <LabeledIconButton icon="more_horiz" label="更多" ariaLabel="更多操作" />
      <div className={styles.popover}>
        <div className={styles.panel} role="menu">
          {item('inventory_2', '打包', '包历史', () => {
            if (workspaceId) {
              setWorkspacePackageDialogOpen(true)
            }
          })}
          {item(
            'analytics',
            '用量',
            'Token 使用分析',
            go(`/workspaces/${workspaceId}/token-usage`)
          )}
          {item(
            'add_task',
            '质量',
            '质量闭环',
            go(`/workspaces/${workspaceId}/quality`)
          )}
          {isAdmin &&
            item(
              'account_tree',
              'Studio',
              'Workflow Studio',
              go(`/workspaces/${workspaceId}/workflow-studio`)
            )}
          {item(
            'settings',
            '设置',
            '设置',
            go(`/workspaces/${workspaceId}/settings`)
          )}
        </div>
      </div>
    </div>
  )
}
