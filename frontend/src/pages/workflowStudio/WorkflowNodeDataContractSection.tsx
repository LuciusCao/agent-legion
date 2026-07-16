import type { WorkflowNodeRecord } from '../../types'
import { WorkflowInspectorDisclosure } from './WorkflowInspectorDisclosure'
import { ItemList } from './WorkflowNodeInspectorLists'
import { WorkflowNodeDataEditor } from './components/WorkflowNodeDataEditor'

export function WorkflowNodeDataContractSection(props: {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  const count = props.node.inputs.length + props.node.outputs.length
  return (
    <WorkflowInspectorDisclosure title="数据契约" summary={`${count} 个产物`}>
      {props.readOnly ? (
        <>
          <ItemList items={props.node.inputs} />
          <ItemList items={props.node.outputs} />
        </>
      ) : (
        <WorkflowNodeDataEditor {...props} />
      )}
    </WorkflowInspectorDisclosure>
  )
}
