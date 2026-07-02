import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  fetchWorkflowDefinition,
  publishWorkflowDraft,
  validateWorkflowDraft,
} from '../api'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { DagGraph } from '../components/DagGraph'
import type { DagGraphEdge, DagGraphNode } from '../components/DagGraph'
import type { WorkflowDefinitionRecord } from '../types'
import styles from './WorkflowStudioPage.module.css'

export function WorkflowStudioPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const [workflow, setWorkflow] = useState<WorkflowDefinitionRecord | null>(
    null
  )
  const [definitionYaml, setDefinitionYaml] = useState('')
  const [validationErrors, setValidationErrors] = useState<string[]>([])
  const [validationMessage, setValidationMessage] = useState('')

  useEffect(() => {
    // MVP fallback: editable Studio must load the workspace active revision
    // from /workspaces/{workspaceId}/workflow-revisions.
    void fetchWorkflowDefinition('question_comprehension_info').then(
      (result) => {
        setWorkflow(result.workflow)
      }
    )
  }, [])

  const nodes: DagGraphNode[] = useMemo(
    () =>
      workflow?.nodes.map((node) => ({
        key: node.key,
        label: node.label,
        status: 'pending',
        created_at: '',
        capability: node.capability,
        inputs: node.inputs,
        outputs: node.outputs,
      })) ?? [],
    [workflow]
  )

  const edges: DagGraphEdge[] = useMemo(
    () =>
      workflow?.edges.map((edge) => ({
        from: edge.source,
        to: edge.target,
      })) ?? [],
    [workflow]
  )

  async function validateDraft() {
    if (!workspaceId) return
    const result = await validateWorkflowDraft(workspaceId, definitionYaml)
    setValidationErrors(result.errors)
    setValidationMessage(result.valid ? '校验通过' : '校验失败')
  }

  async function publishDraft() {
    if (!workspaceId) return
    const result = await publishWorkflowDraft(workspaceId, definitionYaml)
    setValidationErrors(result.errors)
    setValidationMessage(result.valid ? '发布成功' : '发布失败')
  }

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title="Workflow Studio"
          backTo={workspaceId ? `/workspaces/${workspaceId}/settings` : '/'}
          scrolled={scrolled}
        />
      )}
    >
      <div className={styles.layout}>
        <aside className={styles.sidePanel}>
          <h2>节点</h2>
          {workflow?.nodes.map((node) => (
            <button key={node.key} className={styles.nodeButton}>
              {node.label}
            </button>
          ))}
        </aside>
        <main className={styles.canvas}>
          <div className={styles.actions}>
            <button type="button" onClick={() => void validateDraft()}>
              校验
            </button>
            <button type="button" onClick={() => void publishDraft()}>
              发布
            </button>
          </div>
          <h1>{workflow?.label ?? '工作流'}</h1>
          {workflow && <DagGraph nodes={nodes} edges={edges} />}
        </main>
        <aside className={styles.sidePanel}>
          <h2>属性</h2>
          <textarea
            className={styles.yamlEditor}
            value={definitionYaml}
            onChange={(e) => setDefinitionYaml(e.target.value)}
            placeholder="在此粘贴工作流 YAML"
            rows={20}
          />
          {validationMessage && (
            <div className={styles.validationMessage}>{validationMessage}</div>
          )}
          {validationErrors.length > 0 && (
            <ul className={styles.errorList}>
              {validationErrors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </AppShell>
  )
}
