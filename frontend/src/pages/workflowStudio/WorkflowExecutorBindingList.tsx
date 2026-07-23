import type { ExecutorDefinition } from '../../types/executorTypes'
import styles from './WorkflowExecutorBindingList.module.css'

export type CapabilityBinding = {
  executor: ExecutorDefinition
  detail: NonNullable<ExecutorDefinition['capability_details']>[number]
}

export function WorkflowExecutorBindingList(props: {
  bindings: CapabilityBinding[]
}) {
  return (
    <div className={styles.bindingList}>
      {props.bindings.map(({ executor, detail }) => (
        <article
          className={styles.binding}
          key={`${executor.id}:${detail.name}`}
        >
          <div className={styles.bindingHeader}>
            <span className={styles.kind}>执行器</span>
            <span>{executor.id}</span>
          </div>
          <dl className={styles.bindingFields}>
            {detail.handler && (
              <BindingField label="Handler" value={detail.handler} />
            )}
            {detail.skill && (
              <BindingField label="Skill" value={detail.skill} />
            )}
            {detail.tools && detail.tools.length > 0 && (
              <BindingField label="Tools" value={detail.tools.join(', ')} />
            )}
          </dl>
          {detail.skill && (
            <div className={styles.version}>
              {detail.skill_ref || '未锁定版本'}
              {detail.skill_commit && ` · ${detail.skill_commit.slice(0, 7)}`}
            </div>
          )}
        </article>
      ))}
    </div>
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

export function findCapabilityBindings(
  executors: ExecutorDefinition[],
  capability: string
): CapabilityBinding[] {
  return executors.flatMap((executor) =>
    (executor.capability_details ?? [])
      .filter((detail) => detail.name === capability)
      .map((detail) => ({ executor, detail }))
  )
}
