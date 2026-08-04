import { WorkflowStringErrorGroup } from './WorkflowStringErrorGroup'
import type { ValidationGroups } from '../workflowStudioModel'

type Props = {
  groups: ValidationGroups
}

export function WorkflowValidationPanelStringGroups({ groups }: Props) {
  return (
    <>
      <WorkflowStringErrorGroup title="YAML解析" errors={groups.yaml} />
      <WorkflowStringErrorGroup title="结构校验" errors={groups.schema} />
      <WorkflowStringErrorGroup title="结构" errors={groups.structure} />
      <WorkflowStringErrorGroup title="执行器绑定" errors={groups.executor} />
      <WorkflowStringErrorGroup title="版本" errors={groups.revision} />
    </>
  )
}
