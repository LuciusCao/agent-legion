/**
 * 左栏内容预览分区（issue #328）：
 * - workspace 有已发布预览面板 bundle → 沙箱 iframe 渲染它（整栏接管）；
 * - 无 → 渲染 fallback（question 的内置 bundle / 通用产物预览，由调用方组装）；
 * - 「定制预览」按钮唤起 Studio 对话（agent 写草稿）；草稿**不自动执行**
 *   （#347 P1）：agent（或提示注入产物）写入的 HTML 只有在当前用户显式点
 *   「预览此草稿」后才作为 srcDoc 挂载——右栏聊天会给出草稿元信息提示，
 *   点击预览是逐次授权：重开对话框、草稿消失（发布/归档的 null 过渡）、
 *   切换 job/workspace 三者都使授权失效，新草稿/新上下文不继承旧授权
 *   （避免一次点击永久放行）。预览中的草稿实时跟随轮询更新（仅当前
 *   用户可见），发布永远是人工动作。
 * 定制入口 admin-only（与 WorkflowStudioButton 同一惯例，P4/STUDIO-AGENT-001：
 * 治理面端点本身 admin/scoped-only，非 admin 点开只会收获一串 403）。
 */
import { useEffect, useState, type ReactNode } from 'react'
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
  // #347 P1：草稿执行是逐次授权——仅在显式点击「预览此草稿」后为 true，
  // 失效路径（关闭对话框 / 草稿 null 过渡 / 身份参数变化）见下方 effect。
  const [previewDraft, setPreviewDraft] = useState(false)
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const publishedQuery = usePublishedPreviewPanel(workspaceId)
  // 治理面状态查询只在 admin 打开对话框时启用：非 admin 永远不发 403 轮询。
  const stateQuery = usePreviewPanelState(workspaceId, customizing && isAdmin)
  const published = publishedQuery.data ?? null
  const draft = stateQuery.data?.draft ?? null

  // 对话开着且已显式预览且有草稿 → 左栏渲染草稿（仅自己可见）；否则渲染
  // 已发布版本。save_draft 覆盖同一草稿（draft 持续非 null，内容更新）
  // 不受失效路径影响，预览继续实时跟随。
  const draftPreview = customizing && isAdmin && previewDraft && draft !== null
  const bundle = draftPreview ? draft.html : published?.html

  // 授权失效的三条路径（review P1 / 轮 2 P1）：
  // 1. 草稿经 **null 过渡**消失（发布/归档，都是用户可见的人工动作）——
  //    draft 经轮询异步送达，只能在 effect 里观察；
  // 2. 对话框关闭（customizing 翻 false）：draftPreview 已因它同步回落，
  //    这里收尾复位授权，重开对话框即默认态；
  // 3. 身份参数变化：路由仅参数变化（jobs/:jobId）时 react-router 复用
  //    组件实例、本地 state 存活，draft 又可能全程非 null（同 workspace
  //    切 job 查询 key 不变 / 跨 workspace 命中 react-query 缓存）——已
  //    授权草稿（或另一份从未授权的缓存草稿）会在新 jobId 的桥上下文里
  //    继续执行，身份一变即复位。
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 轮询送达的 draft null 过渡 / 对话框关闭使授权失效（review P1）
    if (previewDraft && (draft === null || !customizing)) setPreviewDraft(false)
  }, [customizing, previewDraft, draft])
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 身份参数（jobId/workspaceId）变化使授权失效（review 轮 2 P1）
    setPreviewDraft(false)
  }, [jobId, workspaceId])

  const closeCustomizing = () => setCustomizing(false)

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
          previewDraft={previewDraft}
          onPreviewDraft={() => setPreviewDraft(true)}
          onClose={closeCustomizing}
        />
      )}
    </section>
  )
}
