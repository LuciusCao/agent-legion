import type { ExecutorDefinition } from '../../executorTypes'
import type { WorkflowDefinitionRecord } from '../../types'
import { WorkflowNodeInspector } from './WorkflowNodeInspector'
import styles from './WorkflowStudioRightPanel.module.css'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  executorCatalog: ExecutorDefinition[]
  selectedNodeKey: string | null
  readOnly: boolean
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
}

export function WorkflowStudioRightPanel(props: Props) {
  return (
    <section className={styles.panel} aria-label="节点配置">
      <div className={styles.body}>
        <WorkflowNodeInspector
          workflow={props.workflow}
          executorCatalog={props.executorCatalog}
          selectedNodeKey={props.selectedNodeKey}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          readOnly={props.readOnly}
        />
      </div>
    </section>
  )
}
