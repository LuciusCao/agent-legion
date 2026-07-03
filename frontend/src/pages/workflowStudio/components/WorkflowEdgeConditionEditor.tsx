import type { components } from '../../../generated/api'
import {
  parseWorkflowEdgeConditions,
  patchWorkflowEdgeCondition,
} from '../workflowStudioYamlDraft'
import { resolveEdgeGlobalIndices } from '../workflowStudioEdgeIndices'
import {
  coerceEquals,
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
  const globalIndices = resolveEdgeGlobalIndices(edges, draftEdges)

  return (
    <section
      aria-label="Workflow edge condition editor"
      className={styles.structuredSection}
    >
      <h3 className={styles.structuredTitle}>分支条件编辑</h3>
      {edges.map((edge, localIndex) => {
        const globalIndex = globalIndices[localIndex]
        if (globalIndex < 0) return null
        const draftCondition = draftEdges[globalIndex]?.condition
        return (
          <div
            key={`edge-condition-${globalIndex}`}
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
                value={draftCondition?.artifact ?? ''}
                onChange={(event) =>
                  onDefinitionYamlChange(
                    patchWorkflowEdgeCondition(definitionYaml, globalIndex, {
                      artifact: event.target.value || undefined,
                      path: draftCondition?.path ?? '',
                      equals: coerceEquals(draftCondition?.equals),
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
                    patchWorkflowEdgeCondition(definitionYaml, globalIndex, {
                      artifact: draftCondition?.artifact,
                      path: event.target.value,
                      equals: coerceEquals(draftCondition?.equals),
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
                    patchWorkflowEdgeCondition(definitionYaml, globalIndex, {
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
                  patchWorkflowEdgeCondition(definitionYaml, globalIndex, null)
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
