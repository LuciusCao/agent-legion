import styles from './WorkflowChannelBadge.module.css'

/**
 * M48 双通道徽标（用户确认原型）：Inspector 各 section 标题尾部标明通道
 * 归属——结构通道（发布产生新版本后生效）/ 运行通道（即改即存，不产生
 * 新版本）。
 */
export function WorkflowChannelBadge(props: { kind: 'structure' | 'runtime' }) {
  return (
    <span
      className={
        props.kind === 'runtime' ? styles.badgeRuntime : styles.badgeStructure
      }
    >
      {props.kind === 'runtime' ? '运行通道 · 即改即存' : '结构通道 · 发布生效'}
    </span>
  )
}
