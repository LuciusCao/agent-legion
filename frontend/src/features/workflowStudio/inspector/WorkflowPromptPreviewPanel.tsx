import {
  useNodePromptPreview,
  type NodePromptPreviewPanelProps,
} from './useNodePromptPreview'
import { WorkflowNodeEffectivePrompt } from './WorkflowNodeEffectivePrompt'
import { WorkflowNodePromptEditor } from './WorkflowNodePromptEditor'
import styles from './WorkflowPromptPreviewPanel.module.css'

/** 详情 panel 内的运行 Prompt 面板（原位替换 inspector，不开 dialog）：
 * 「节点指令」默认由后端按节点信息自动组装（execution.prompt 留空），编辑即
 * 整段替代默认指令并写入草稿 YAML，可一键重置；下方是含平台信封的完整运行
 * Prompt 只读预览。导航（返回/面包屑）在 DetailView，本面板无自带头栏。 */
export function WorkflowPromptPreviewPanel(props: NodePromptPreviewPanelProps) {
  const { editor, effectivePrompt } = useNodePromptPreview(props)
  return (
    <section aria-label="Prompt 预览" className={styles.panel}>
      <WorkflowNodePromptEditor {...editor} />
      <WorkflowNodeEffectivePrompt effectivePrompt={effectivePrompt} />
    </section>
  )
}
