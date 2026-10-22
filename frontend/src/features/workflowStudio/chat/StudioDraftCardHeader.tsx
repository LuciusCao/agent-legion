import type { ReactNode } from 'react'
import type { SvgIconProps } from '@mui/material'
import styles from './StudioChatPanel.module.css'

/* #692：草稿卡共用头部——MUI 线性图标 + 类型色，一眼区分 agent 产出的
 * 三种草稿（Workflow 定义 / Agent 定义 / 节点代码）。图标尺寸与
 * inspector 面板的小号线性图标对齐（fontSize 18）；类型色调为本 PR
 * 新引入（见 StudioChatPanel.module.css 的对比度说明）。
 * M65 视觉返工 r4：badge 作为标题行的独立 flex 子项渲染（不包进标题
 * 文字的 span）——行是 align-items: center 的 flex，标题文字与徽标的
 * 中心线由此像素级对齐（徽标内联在文字里时只剩一个文本空格的间距，
 * 且走基线对齐会光学错位）。 */

export function StudioDraftCardHeader({
  icon: Icon,
  tone,
  badge,
  children,
}: {
  icon: (props: SvgIconProps) => ReactNode
  tone: 'workflow' | 'agent' | 'code'
  /** 通道徽标（可选）：作为标题行独立 flex 子项，与标题文字精确垂直居中。 */
  badge?: ReactNode
  children: ReactNode
}) {
  return (
    <div className={`${styles.draftTitle} ${styles[`draftTitle_${tone}`]}`}>
      <Icon className={styles[`draftIcon_${tone}`]} />
      <span>{children}</span>
      {badge}
    </div>
  )
}
