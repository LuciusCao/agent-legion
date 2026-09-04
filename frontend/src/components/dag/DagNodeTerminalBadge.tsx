import styles from './DagNodeBadges.module.css'

/**
 * 终态徽标（#423 review P2 拆分）：terminal.outcome 是用户工作流里的自由
 * 文本（后端只校验非空），属于动态内容徽标——样式来自
 * DagNodeBadges.module.css（可收缩 + max-width + 单行省略，长 outcome 不
 * 会把 .label 挤到零宽或伸出 280px 卡片），title 兜底全文。
 */
export function DagNodeTerminalBadge({ outcome }: { outcome: string }) {
  return (
    <span className={styles.terminalTag} title={outcome}>
      {outcome}
    </span>
  )
}
