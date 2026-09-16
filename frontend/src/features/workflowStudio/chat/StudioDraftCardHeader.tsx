import type { ReactNode } from 'react'
import type { SvgIconProps } from '@mui/material'
import styles from './StudioChatPanel.module.css'

/* #692：草稿卡共用头部——MUI 线性图标 + 类型色，一眼区分 agent 产出的
 * 三种草稿（Workflow 定义 / Agent 定义 / 节点代码）。图标尺寸与
 * inspector 面板的小号线性图标对齐（fontSize 18），色板沿用各类型在
 * 画布/检查器里的既有语义色。 */

export function StudioDraftCardHeader({
  icon: Icon,
  tone,
  children,
}: {
  icon: (props: SvgIconProps) => ReactNode
  tone: 'workflow' | 'agent' | 'code'
  children: ReactNode
}) {
  return (
    <div className={`${styles.draftTitle} ${styles[`draftTitle_${tone}`]}`}>
      <Icon className={styles[`draftIcon_${tone}`]} />
      <span>{children}</span>
    </div>
  )
}
