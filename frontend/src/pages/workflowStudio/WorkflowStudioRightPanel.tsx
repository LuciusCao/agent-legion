import { Tab, Tabs } from '@mui/material'
import { useEffect, useRef, useState } from 'react'
import type { WorkflowDefinitionRecord } from '../../types'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import { WorkflowChangeSummaryPanel } from './components/WorkflowChangeSummaryPanel'
import { WorkflowDefinitionEditor } from './WorkflowDefinitionEditor'
import { WorkflowNodeInspector } from './WorkflowNodeInspector'
import { WorkflowValidationPanel } from './WorkflowValidationPanel'
import styles from './WorkflowStudioRightPanel.module.css'

type PanelMode = 'overview' | 'node' | 'changes' | 'yaml' | 'validation'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  selectedNodeKey: string | null
  readOnly: boolean
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  compareSummary: ChangeSummaryViewModel | null
  compareState: 'idle' | 'loading' | 'ready' | 'error'
  compareErrors:
    | import('../../generated/api').components['schemas']['WorkflowDraftCompareError'][]
    | null
  validationMessage: string
  validationErrors: string[]
  onSelectNode: (nodeKey: string | null) => void
}

function workflowNodeCount(workflow: WorkflowDefinitionRecord | null): number {
  return workflow?.nodes.length ?? 0
}

function workflowEdgeCount(workflow: WorkflowDefinitionRecord | null): number {
  return workflow?.edges.length ?? 0
}

export function WorkflowStudioRightPanel(props: Props) {
  const [mode, setMode] = useState<PanelMode>('overview')

  useEffect(() => {
    if (props.selectedNodeKey) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setMode('node')
    }
  }, [props.selectedNodeKey])

  const previousValidationMessage = useRef(props.validationMessage)
  useEffect(() => {
    if (props.validationErrors.length > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setMode('validation')
    } else if (
      props.validationMessage &&
      previousValidationMessage.current === ''
    ) {
      setMode('validation')
    }
    previousValidationMessage.current = props.validationMessage
  }, [props.validationErrors.length, props.validationMessage])

  return (
    <section className={styles.panel} aria-label="Workflow inspector modes">
      <Tabs
        value={mode}
        onChange={(_, value: PanelMode) => setMode(value)}
        variant="scrollable"
        scrollButtons="auto"
        className={styles.tabs}
      >
        <Tab value="overview" label="Overview" />
        <Tab value="node" label="Node" disabled={!props.selectedNodeKey} />
        <Tab value="changes" label="Changes" />
        <Tab value="yaml" label="YAML" />
        <Tab value="validation" label="Validation" />
      </Tabs>
      <div className={styles.body}>
        {mode === 'overview' && (
          <section aria-label="Workflow overview" className={styles.overview}>
            <h2>{props.workflow?.label ?? 'Workflow'}</h2>
            <dl>
              <div>
                <dt>节点</dt>
                <dd>{workflowNodeCount(props.workflow)}</dd>
              </div>
              <div>
                <dt>连线</dt>
                <dd>{workflowEdgeCount(props.workflow)}</dd>
              </div>
              <div>
                <dt>模式</dt>
                <dd>{props.readOnly ? '只读历史版本' : '可编辑草稿'}</dd>
              </div>
            </dl>
          </section>
        )}
        {mode === 'node' && (
          <WorkflowNodeInspector
            workflow={props.workflow}
            selectedNodeKey={props.selectedNodeKey}
            readOnly={props.readOnly}
          />
        )}
        {mode === 'changes' && (
          <WorkflowChangeSummaryPanel
            summary={props.compareSummary}
            loading={props.compareState === 'loading'}
            errors={props.compareErrors}
            onSelectNode={props.onSelectNode}
          />
        )}
        {mode === 'yaml' && (
          <WorkflowDefinitionEditor
            value={props.definitionYaml}
            onChange={props.setDefinitionYaml}
            readOnly={props.readOnly}
            label={props.readOnly ? 'Revision YAML' : '高级 YAML 编辑器'}
          />
        )}
        {mode === 'validation' && (
          <WorkflowValidationPanel
            message={props.validationMessage}
            errors={props.validationErrors}
            compareErrors={props.compareErrors ?? undefined}
            onSelectNode={props.onSelectNode}
          />
        )}
      </div>
    </section>
  )
}
