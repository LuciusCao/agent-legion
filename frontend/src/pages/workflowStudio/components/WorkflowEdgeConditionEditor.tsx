import type { components } from '../../../generated/api'
import {
  parseWorkflowEdgeConditions,
  patchWorkflowEdgeCondition,
} from '../workflowStudioYamlDraft'

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

  const draftEdges = parseWorkflowEdgeConditions(definitionYaml)

  return (
    <section
      aria-label="Workflow edge condition editor"
      className={styles.structuredSection}
    >
      <h3 className={styles.structuredTitle}>分支条件编辑</h3>
      {edges.map((edge, index) => {
        const draftCondition = draftEdges[index]?.condition
        return (
          <div key={`edge-condition-${index}`} className={styles.fieldGroup}>
            <div className={styles.fieldGroupTitle}>
              {edge.source} → {edge.target}
            </div>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>条件 artifact（可选）</span>
              <input
                aria-label="条件 artifact"
                className={styles.fieldInput}
                value={draftCondition?.artifact ?? ''}
                onChange={(event) =>
                  onDefinitionYamlChange(
                    patchWorkflowEdgeCondition(definitionYaml, index, {
                      artifact: event.target.value || undefined,
                      path: draftCondition?.path ?? '',
                      equals: (draftCondition?.equals ?? '') as
                        | string
                        | number
                        | boolean
                        | null,
                    })
                  )
                }
              />
            </label>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>条件 path</span>
              <input
                aria-label="条件 path"
                className={styles.fieldInput}
                value={draftCondition?.path ?? ''}
                onChange={(event) =>
                  onDefinitionYamlChange(
                    patchWorkflowEdgeCondition(definitionYaml, index, {
                      artifact: draftCondition?.artifact,
                      path: event.target.value,
                      equals: (draftCondition?.equals ?? '') as
                        | string
                        | number
                        | boolean
                        | null,
                    })
                  )
                }
              />
            </label>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>条件 equals</span>
              <input
                aria-label="条件 equals"
                className={styles.fieldInput}
                value={formatEquals(draftCondition?.equals)}
                onChange={(event) =>
                  onDefinitionYamlChange(
                    patchWorkflowEdgeCondition(definitionYaml, index, {
                      artifact: draftCondition?.artifact,
                      path: draftCondition?.path ?? '',
                      equals: parseEquals(event.target.value),
                    })
                  )
                }
              />
            </label>
            <button
              type="button"
              className={styles.fieldButton}
              onClick={() =>
                onDefinitionYamlChange(
                  patchWorkflowEdgeCondition(definitionYaml, index, null)
                )
              }
            >
              清除条件
            </button>
          </div>
        )
      })}
    </section>
  )
}
