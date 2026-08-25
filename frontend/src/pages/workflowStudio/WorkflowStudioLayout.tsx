import { WorkflowStudioLayoutDialogs } from './WorkflowStudioLayoutDialogs'
import { WorkflowStudioWorkspace } from './WorkflowStudioWorkspace'
import { useStudioState } from './studioStateContext'
import basePageStyles from '../WorkflowStudioPage.module.css'

export function WorkflowStudioLayout() {
  const studio = useStudioState()
  return (
    <>
      <div className={basePageStyles.page}>
        {studio.loadState === 'loading' && <p>正在加载 workflow</p>}
        {studio.loadState === 'error' && (
          <p>无法加载 active workflow revision</p>
        )}
        {(studio.loadState === 'ready' || studio.loadState === 'empty') && (
          <WorkflowStudioWorkspace />
        )}
      </div>
      <WorkflowStudioLayoutDialogs />
    </>
  )
}
