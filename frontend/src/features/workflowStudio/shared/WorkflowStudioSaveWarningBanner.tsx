import {
  useDraftSaveConflictActions,
  WorkflowStudioDraftSaveControl,
} from './WorkflowStudioDraftSaveControl'
import { useStudioStateOptional, type StudioState } from './studioStateContext'

/** 抽屉内警示横幅（#804 轮 4 P2-F）：节点详情/共享素材抽屉是 z1300
 * Modal，打开时盖住 z900 左岛——冲突/保存终态失败/服务不可用在抽屉打开
 * 期间完全不可见（toast 3s 自消不可靠）。在抽屉内容顶部内嵌同源的精简
 * 警示条（warningsOnly：只要警示态，瞬态「保存中…」不出现）。无警示时
 * 不渲染（不占抽屉头部空间）。 */
export function WorkflowStudioSaveWarningBanner() {
  const studio = useStudioStateOptional()
  if (!studio) return null
  return <SaveWarningBannerInner studio={studio} />
}

function SaveWarningBannerInner({ studio }: { studio: StudioState }) {
  const actions = useDraftSaveConflictActions()
  const save = studio.draftSave
  const hasWarning =
    save?.conflict === true ||
    save?.loadError === true ||
    save?.status === 'error'
  if (!hasWarning) return null
  return (
    <div
      role="alert"
      style={{
        background: '#fdecea',
        borderBottom: '1px solid #f5c2c0',
        padding: '8px 12px',
      }}
    >
      <WorkflowStudioDraftSaveControl
        save={save}
        readOnly={studio.readOnly}
        warningsOnly
        {...actions}
      />
    </div>
  )
}
