import type { components } from '../../../generated/api'
import { patchWorkflowEdgeCondition } from '../workflowStudioYamlDraft'

import {
  formatEquals,
  parseEquals,
} from './WorkflowEdgeConditionEditor.helpers'
import styles from './WorkflowStructuredEditor.module.css'

type Props = {
  edges: components['schemas']['WorkflowEdgeResponse'][]
  definitionYaml: string
  onDefinitionYamlChange: (nextYaml: string) => void
}

export function WorkflowEdgeConditionEditor({
  edges,
  definitionYaml,
  onDefinitionYamlChange,
}: Props) {
  if (edges.length === 0) return null

  return (
    <section
      aria-label="Workflow edge condition editor"
      className={styles.structuredSection}
    >
      <h3 className={styles.structuredTitle}>分支条件编辑</h3>
      {edges.map((edge) => (
        <div
          key={`${edge.source}-${edge.target}`}
          className={styles.fieldGroup}
        >
          <div className={styles.fieldGroupTitle}>
            {edge.source} → {edge.target}
          </div>
          <label className={styles.field}>
            <span className={styles.fieldLabel}>条件 artifact（可选）</span>
            <input
              aria-label="条件 artifact"
              className={styles.fieldInput}
              value={edge.condition?.artifact ?? ''}
              onChange={(event) =>
                onDefinitionYamlChange(
                  patchWorkflowEdgeCondition(
                    definitionYaml,
                    edge.source,
                    edge.target,
                    {
                      artifact: event.target.value || undefined,
                      path: edge.condition?.path ?? '',
                      equals: (edge.condition?.equals ?? '') as
                        | string
                        | number
                        | boolean
                        | null,
                    }
                  )
                )
              }
            />
          </label>
          <label className={styles.field}>
            <span className={styles.fieldLabel}>条件 path</span>
            <input
              aria-label="条件 path"
              className={styles.fieldInput}
              value={edge.condition?.path ?? ''}
              onChange={(event) =>
                onDefinitionYamlChange(
                  patchWorkflowEdgeCondition(
                    definitionYaml,
                    edge.source,
                    edge.target,
                    {
                      artifact: edge.condition?.artifact,
                      path: event.target.value,
                      equals: (edge.condition?.equals ?? '') as
                        | string
                        | number
                        | boolean
                        | null,
                    }
                  )
                )
              }
            />
          </label>
          <label className={styles.field}>
            <span className={styles.fieldLabel}>条件 equals</span>
            <input
              aria-label="条件 equals"
              className={styles.fieldInput}
              value={formatEquals(edge.condition?.equals)}
              onChange={(event) =>
                onDefinitionYamlChange(
                  patchWorkflowEdgeCondition(
                    definitionYaml,
                    edge.source,
                    edge.target,
                    {
                      artifact: edge.condition?.artifact,
                      path: edge.condition?.path ?? '',
                      equals: parseEquals(event.target.value),
                    }
                  )
                )
              }
            />
          </label>
          <button
            type="button"
            className={styles.fieldButton}
            onClick={() =>
              onDefinitionYamlChange(
                patchWorkflowEdgeCondition(
                  definitionYaml,
                  edge.source,
                  edge.target,
                  null
                )
              )
            }
          >
            清除条件
          </button>
        </div>
      ))}
    </section>
  )
}
