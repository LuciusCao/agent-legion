import { WorkflowStudioErrorDetails } from './WorkflowStudioErrorDetails'
import {
  useDraftSaveConflictActions,
  WorkflowStudioDraftSaveControl,
} from './WorkflowStudioDraftSaveControl'
import { useStudioStateOptional, type StudioState } from './studioStateContext'
import styles from './WorkflowStudioSaveWarningBanner.module.css'

/** 保存/发布拒绝的持久警示。画布与抽屉共用同一数据源，完整保留服务端
 * 迁移指引，不依赖会自动消失的 toast。 */
export function WorkflowStudioSaveWarningBanner(props: { floating?: boolean }) {
  const studio = useStudioStateOptional()
  if (!studio) return null
  return <SaveWarningBannerInner studio={studio} floating={props.floating} />
}

function SaveWarningBannerInner({
  studio,
  floating,
}: {
  studio: StudioState
  floating?: boolean
}) {
  const actions = useDraftSaveConflictActions()
  const save = studio.draftSave
  const hasWarning =
    save?.conflict === true ||
    save?.loadError === true ||
    save?.status === 'error' ||
    Boolean(studio.runtimeError) ||
    Boolean(studio.publishError)
  if (!hasWarning) return null
  return (
    <div
      role={
        save?.saveErrorMessage || studio.runtimeError || studio.publishError
          ? undefined
          : 'alert'
      }
      className={`${styles.banner} ${floating ? styles.floating : ''}`}
    >
      <WorkflowStudioDraftSaveControl
        save={save}
        readOnly={studio.readOnly}
        warningsOnly
        {...actions}
      />
      <WorkflowStudioErrorDetails {...studio} />
    </div>
  )
}
