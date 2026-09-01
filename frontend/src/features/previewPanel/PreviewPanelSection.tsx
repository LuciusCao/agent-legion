/**
 * 左栏内容预览分区（issue #328）：
 * - workspace 有已发布预览面板 bundle → 沙箱 iframe 渲染它（整栏接管）；
 * - 无 → 渲染 fallback（question 的内置 bundle / 通用产物预览，由调用方组装）；
 * - 「定制预览」按钮唤起 Studio 对话（agent 写草稿）；对话期间左栏实时渲染
 *   草稿——这是本页面的客户端状态，只对当前用户可见，发布永远是人工动作。
 */
import { useState, type ReactNode } from 'react'
import { PreviewPanelHost } from './PreviewPanelHost'
import { CustomizePreviewDialog } from './CustomizePreviewDialog'
import {
  usePreviewPanelState,
  usePublishedPreviewPanel,
} from './usePreviewPanel'
import styles from './PreviewPanelSection.module.css'

export interface PreviewPanelSectionProps {
  jobId: string
  workspaceId?: string
  /** 未定制 workspace 的现有左栏内容（回落路径，扩展名分发不变）。 */
  fallback: ReactNode
}

export function PreviewPanelSection({
  jobId,
  workspaceId,
  fallback,
}: PreviewPanelSectionProps) {
  const [customizing, setCustomizing] = useState(false)
  const publishedQuery = usePublishedPreviewPanel(workspaceId)
  const stateQuery = usePreviewPanelState(workspaceId, customizing)
  const published = publishedQuery.data ?? null
  const draft = stateQuery.data?.draft ?? null

  // 对话开着且有草稿 → 左栏渲染草稿（仅自己可见）；否则渲染已发布版本。
  const draftPreview = customizing && draft !== null
  const bundle = draftPreview ? draft.html : published?.html

  return (
    <section className={styles.root} data-testid="preview-panel-section">
      {workspaceId && (
        <header className={styles.header}>
          <h2 className={styles.title}>内容预览</h2>
          {draftPreview && (
            <span className={styles.draftBadge}>草稿预览中</span>
          )}
          <button
            type="button"
            className={styles.customizeButton}
            onClick={() => setCustomizing(true)}
          >
            定制预览
          </button>
        </header>
      )}
      {bundle ? (
        <PreviewPanelHost key={jobId} jobId={jobId} html={bundle} />
      ) : (
        fallback
      )}
      {customizing && workspaceId && (
        <CustomizePreviewDialog
          workspaceId={workspaceId}
          state={stateQuery.data ?? null}
          onClose={() => setCustomizing(false)}
        />
      )}
    </section>
  )
}
