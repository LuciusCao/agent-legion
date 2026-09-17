import type { ReactNode } from 'react'
import type { SvgIconProps } from '@mui/material'
import styles from './StudioChatPanel.module.css'

/* #692：草稿卡共用头部——MUI 线性图标 + 类型色，一眼区分 agent 产出的
 * 三种草稿（Workflow 定义 / Agent 定义 / 节点代码）。图标尺寸与
 * inspector 面板的小号线性图标对齐（fontSize 18）；类型色调为本 PR
 * 新引入（见 StudioChatPanel.module.css 的对比度说明）。 */

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
