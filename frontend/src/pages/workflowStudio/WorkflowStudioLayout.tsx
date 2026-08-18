import { WorkflowStudioLayoutDialogs } from './WorkflowStudioLayoutDialogs'
import { WorkflowStudioWorkspace } from './WorkflowStudioWorkspace'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import basePageStyles from '../WorkflowStudioPage.module.css'

export function WorkflowStudioLayout(props: StudioLayoutProps) {
  return (
    <>
      <div className={basePageStyles.page}>
        {props.loadState === 'loading' && <p>正在加载 workflow</p>}
        {props.loadState === 'error' && (
          <p>无法加载 active workflow revision</p>
        )}
        {(props.loadState === 'ready' || props.loadState === 'empty') && (
          <WorkflowStudioWorkspace {...props} />
        )}
      </div>
      <WorkflowStudioLayoutDialogs {...props} />
    </>
  )
}
