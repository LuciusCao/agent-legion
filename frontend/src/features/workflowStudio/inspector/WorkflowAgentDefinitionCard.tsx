import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import styles from './WorkflowExecutorBindingList.module.css'

/** 节点绑定 Agent 的只读摘要卡；编辑经节点详情的内嵌 AgentEditor。
 *  #575：Tools 行展示的是 Agent 定义的兜底值——节点未覆盖时标注
 * 「（节点未覆盖，当前生效）」，避免与节点级「Tools 覆盖」字段形成
 * 「两个字段」观感；节点已覆盖时保留原值（它是定义自身的值）。 */
export function WorkflowAgentDefinitionCard(props: {
  definition: AgentDefinition
  /** #575：节点已声明 tools 覆盖时为 true。 */
  nodeToolsOverridden?: boolean
}) {
  const { definition } = props
  const tools = definition.tools ?? []
  const toolsSuffix = props.nodeToolsOverridden
    ? ''
    : '（节点未覆盖，当前生效）'
  return (
    <article className={styles.binding}>
      <div className={styles.bindingHeader}>
        <span className={styles.kind}>Agent</span>
        <span>{definition.id}</span>
      </div>
      <dl className={styles.bindingFields}>
        <BindingField label="Runtime" value={definition.runtime} />
        {/* #76：skill 降为可选 legacy 兜底——空串时后端不再注入 ref/commit，
            卡片同样不渲染该行。 */}
        {definition.skill && (
          <BindingField label="Skill" value={definition.skill} />
        )}
        {tools.length > 0 && (
          <BindingField label="Tools" value={tools.join(', ') + toolsSuffix} />
        )}
      </dl>
      {(definition.skill_ref || definition.skill_commit) && (
        <div className={styles.version}>
          {definition.skill_ref || '未锁定版本'}
          {definition.skill_commit &&
            ` · ${definition.skill_commit.slice(0, 7)}`}
        </div>
      )}
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
