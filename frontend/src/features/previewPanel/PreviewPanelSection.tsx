/**
 * 左栏内容预览分区（issue #328）：
 * - workspace 有已发布预览面板 bundle → 沙箱 iframe 渲染它（整栏接管）；
 * - 无 → 渲染 fallback（question 的内置 bundle / 通用产物预览，由调用方组装）；
 * - 「定制预览」按钮唤起 Studio 对话（agent 写草稿）；对话期间左栏实时渲染
 *   草稿——这是本页面的客户端状态，只对当前用户可见，发布永远是人工动作。
 * 定制入口 admin-only（与 WorkflowStudioButton 同一惯例，P4/STUDIO-AGENT-001：
 * 治理面端点本身 admin/scoped-only，非 admin 点开只会收获一串 403）。
 */
import { useState, type ReactNode } from 'react'
import { useAuthStore } from '../../stores/authStore'
import { PreviewPanelHost } from './PreviewPanelHost'
import { CustomizePreviewDialog } from './CustomizePreviewDialog'
import {
  usePreviewPanelState,
  usePublishedPreviewPanel,
} from './usePreviewPanel'
import styles from './PreviewPanelSection.module.css'

/** key 用的 bundle 稳定指纹（草稿轮询比较内容而非引用，避免无谓重挂）。 */
function hashBundle(html: string): string {
  let hash = 0
  for (let i = 0; i < html.length; i++) {
    hash = (Math.imul(hash, 31) + html.charCodeAt(i)) | 0
  }
  return (hash >>> 0).toString(36)
}

export interface PreviewPanelSectionProps {
  jobId: string
  workspaceId?: string
  /** 未定制 workspace 的现有左栏内容（回落路径，扩展名分发不变）。 */
  fallback: ReactNode
}

export function PreviewPanelSection(props: PreviewPanelSectionProps) {
  const { jobId, workspaceId, fallback } = props
  const [customizing, setCustomizing] = useState(false)
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const publishedQuery = usePublishedPreviewPanel(workspaceId)
  // 治理面状态查询只在 admin 打开对话框时启用：非 admin 永远不发 403 轮询。
  const stateQuery = usePreviewPanelState(workspaceId, customizing && isAdmin)
  const published = publishedQuery.data ?? null
  const draft = stateQuery.data?.draft ?? null

  // 对话开着且有草稿 → 左栏渲染草稿（仅自己可见）；否则渲染已发布版本。
  const draftPreview = customizing && isAdmin && draft !== null
  const bundle = draftPreview ? draft.html : published?.html

  return (
    <section className={styles.root} data-testid="preview-panel-section">
      {workspaceId && (
        <header className={styles.header}>
          <h2 className={styles.title}>内容预览</h2>
          {draftPreview && (
            <span className={styles.draftBadge}>草稿预览中</span>
          )}
          {isAdmin && (
            <button
              type="button"
              className={styles.customizeButton}
              onClick={() => setCustomizing(true)}
            >
              定制预览
            </button>
          )}
        </header>
      )}
      {bundle ? (
        // key 含 bundle 内容（codex P2）：草稿轮询更新 bundle 时若沿用旧
        // iframe，React 在同一 contentWindow 上做 srcDoc 导航——旧文档仍在
        // 途的桥请求会由宿主把响应投递给同一个 WindowProxy，而新文档的
        // 请求编号又从 1 重新计数，旧响应可能错误地应答新文档的同编号
        // 请求。bundle 变化即整树重挂：旧窗口销毁，在途响应无处可投。
        <PreviewPanelHost
          key={`${jobId}:${hashBundle(bundle)}`}
          jobId={jobId}
          html={bundle}
        />
      ) : (
        fallback
      )}
      {customizing && isAdmin && workspaceId && (
        <CustomizePreviewDialog
          workspaceId={workspaceId}
          state={stateQuery.data ?? null}
          onClose={() => setCustomizing(false)}
        />
      )}
    </section>
  )
}
