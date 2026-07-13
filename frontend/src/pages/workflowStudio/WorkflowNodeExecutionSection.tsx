import type { ExecutorDefinition } from '../../executorTypes'
import type { WorkflowNodeRecord } from '../../types'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import styles from './WorkflowNodeExecutionSection.module.css'

type Props = {
  node: WorkflowNodeRecord
  executorCatalog: ExecutorDefinition[]
}

type CapabilityBinding = {
  executor: ExecutorDefinition
  detail: NonNullable<ExecutorDefinition['capability_details']>[number]
}

export function WorkflowNodeExecutionSection({ node, executorCatalog }: Props) {
  const bindings = findCapabilityBindings(executorCatalog, node.capability)
  return (
    <section className={inspectorStyles.section} aria-label="节点执行能力">
      <div className={inspectorStyles.sectionTitle}>执行能力</div>
      {bindings.length === 0 ? (
        <div className={inspectorStyles.empty}>
          未匹配到 executor capability
        </div>
      ) : (
        <div className={styles.bindingList}>
          {bindings.map(({ executor, detail }) => (
            <article
              className={styles.bindingCard}
              key={`${executor.id}:${detail.name}`}
            >
              <div className={styles.bindingHeader}>
                <span>{executor.id}</span>
                <span>{executor.kind}</span>
              </div>
              <dl className={styles.bindingFields}>
                <BindingField label="Capability" value={detail.name} />
                {detail.handler && (
                  <BindingField label="Local Handler" value={detail.handler} />
                )}
                {detail.skill && (
                  <BindingField label="Skill" value={detail.skill} />
                )}
                {detail.tools && detail.tools.length > 0 && (
                  <BindingField label="Tools" value={detail.tools.join(', ')} />
                )}
              </dl>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

function BindingField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

function findCapabilityBindings(
  executors: ExecutorDefinition[],
  capability: string
): CapabilityBinding[] {
  return executors.flatMap((executor) =>
    (executor.capability_details ?? [])
      .filter((detail) => detail.name === capability)
      .map((detail) => ({ executor, detail }))
  )
}
