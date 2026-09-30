import { MaterialIcon } from '../../../components/MaterialIcon'
import { useStudioState, useStudioView } from './studioStateContext'
import styles from './WorkflowStudioMobileNav.module.css'

/** 窄屏常驻警示徽标（#804 轮 4 P1-B）：草稿冲突 / 保存终态失败 / 草稿服务
 * 不可用时钉在页签行右端——岛内的完整警示在画布列里，窄屏切到 Agent
 * 页签时画布列被整列 display:none，没有它会完全隐形（冲突态 flushNow
 * 刻意 no-op，编辑可能静默丢失）。点击切回画布页签定位到岛内完整警示
 * 与操作出口。宽屏不渲染（CSS display:none）。 */
export function WorkflowStudioNarrowAlertBadge() {
  const studio = useStudioState()
  const view = useStudioView()
  const save = studio.draftSave
  const warning = save?.conflict
    ? '草稿冲突待处理'
    : save?.loadError
      ? '草稿服务不可用'
      : save?.status === 'error'
        ? '草稿保存失败'
        : null
  if (!warning) return null
  return (
    <button
      type="button"
      className={styles.narrowAlert}
      aria-label={`${warning}，点击查看`}
      onClick={() => view.setMobilePanel('graph')}
    >
      <MaterialIcon name="warning" fontSize="small" />
      {warning}
    </button>
  )
}
