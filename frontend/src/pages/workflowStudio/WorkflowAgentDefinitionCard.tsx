import { Button } from '@mui/material'
import type { AgentDefinition } from '../../types/executorTypes'
import { useStudioNav } from './workflowStudioNav'
import styles from './WorkflowExecutorBindingList.module.css'

export function WorkflowAgentDefinitionCard(props: {
  definition: AgentDefinition
}) {
  const { definition } = props
  const nav = useStudioNav()
  const tools = definition.tools ?? []
  return (
    <article className={styles.binding}>
      <div className={styles.bindingHeader}>
        <span className={styles.kind}>Agent</span>
        <span>{definition.id}</span>
      </div>
      <dl className={styles.bindingFields}>
        <BindingField label="Runtime" value={definition.runtime} />
        <BindingField label="Skill" value={definition.skill} />
        {tools.length > 0 && (
          <BindingField label="Tools" value={tools.join(', ')} />
        )}
      </dl>
      {(definition.skill_ref || definition.skill_commit) && (
        <div className={styles.version}>
          {definition.skill_ref || '未锁定版本'}
          {definition.skill_commit &&
            ` · ${definition.skill_commit.slice(0, 7)}`}
        </div>
      )}
      <div>
        <Button size="small" onClick={() => nav.openAgent(definition.id)}>
          在 Agent 管理中打开
        </Button>
      </div>
    </article>
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
