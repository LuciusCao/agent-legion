/**
 * 左栏内容预览分区（issue #328）：
 * - workspace 有已发布预览面板 bundle → 沙箱 iframe 渲染它（整栏接管）；
 * - 无 → 渲染 fallback（question 的内置 bundle / 通用产物预览，由调用方组装）；
 * - 「定制预览」按钮唤起 Studio 对话（agent 写草稿）；草稿**不自动执行**
 *   （#347 P1）：agent（或提示注入产物）写入的 HTML 只有在当前用户显式点
 *   「预览此草稿」后才作为 srcDoc 挂载——右栏聊天会给出草稿元信息提示，
 *   点击预览是逐次授权：重开对话框、草稿消失（发布/归档的 null 过渡）、
 *   切换 job/workspace、草稿内容变化（save_draft 覆盖）四者都使授权失效，
 *   新草稿/新上下文不继承旧授权（避免一次点击永久放行）。授权的快照与
 *   render 期派生比对抽在 useDraftAuthorization（#500 P1-3/P1-5）；发布
 *   永远是人工动作。
 * - #615 方向 A：授权后草稿只在左栏渲染（单一通道，#701 的对话框内嵌
 *   预览已撤）；对话框改为非模态覆盖层，停靠右侧盖住 job progress 列，
 *   不遮挡左栏、不锁底层滚动；点「预览此草稿」后左栏滚动定位到面板并
 *   短暂高亮（轻量版方向 C），「改草稿 → 看左栏 → 继续对话」闭环不中断。
 * 定制入口 admin-only（与 WorkspaceMoreMenu 的 Studio 项同一惯例，P4/STUDIO-AGENT-001：
 * 治理面端点本身 admin/scoped-only，非 admin 点开只会收获一串 403）。
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useAuthStore } from '../../stores/authStore'
import { PreviewPanelHost } from './PreviewPanelHost'
import { previewHostKey } from './bundleKey'
import { CustomizePreviewDialog } from './CustomizePreviewDialog'
import {
  usePreviewPanelState,
  usePublishedPreviewPanel,
} from './usePreviewPanel'
import { useDraftAuthorization } from './useDraftAuthorization'
import styles from './PreviewPanelSection.module.css'

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
  // #347 P1 / #500：草稿执行是逐次授权——快照、render 期派生比对与收尾
  // 复位都在 useDraftAuthorization（快照之外的一切 = 未授权）。
  const auth = useDraftAuthorization(jobId, workspaceId, customizing, draft)
  // 对话开着且授权有效且有草稿 → 左栏渲染草稿（仅自己可见）；否则渲染
  // 已发布版本。同一草稿内容（hash 不变）的轮询刷新自动跟随。
  const draftPreview =
    customizing && isAdmin && auth.isAuthorized && draft !== null
  // 当前渲染的版本对象（草稿或已发布）：html 与挂载指纹（html_hash）必须
  // 取自同一版本——指纹是服务端 sha256，前端不自算（codex P2，见 bundleKey）。
  const bundleVersion = draftPreview ? draft : published

  const closeCustomizing = () => setCustomizing(false)

  // #615 方向 C 轻量版：点「预览此草稿」后把左栏滚动定位到面板并短暂高亮，
  // 让草稿渲染落点显而易见（非模态面板已不遮挡左栏，这是引导视线而非解锁）。
  const rootRef = useRef<HTMLElement>(null)
  const [flash, setFlash] = useState(false)
  useEffect(() => {
    if (!flash) return
    const timer = setTimeout(() => setFlash(false), 1600)
    return () => clearTimeout(timer)
  }, [flash])

  return (
    <section
      ref={rootRef}
      className={flash ? `${styles.root} ${styles.flash}` : styles.root}
      data-testid="preview-panel-section"
    >
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
      {bundleVersion?.html ? (
        // key = jobId + 服务端 html_hash（codex P2，指纹抽在 bundleKey）：
        // 草稿轮询更新 bundle 时若沿用旧 iframe，React 在同一 contentWindow
        // 上做 srcDoc 导航——旧文档仍在途的桥请求会由宿主把响应投递给同一
        // 个 WindowProxy，而新文档的请求编号又从 1 重新计数，旧响应可能错误
        // 地应答新文档的同编号请求。内容指纹随版本对象下发（sha256），变化
        // 即整树重挂：旧窗口销毁，在途响应无处可投。
        <PreviewPanelHost
          key={previewHostKey(jobId, bundleVersion.html_hash)}
          jobId={jobId}
          html={bundleVersion.html}
        />
      ) : (
        fallback
      )}
      {customizing && isAdmin && workspaceId && (
        <CustomizePreviewDialog
          workspaceId={workspaceId}
          state={stateQuery.data ?? null}
          previewDraft={auth.isAuthorized && draft !== null}
          onPreviewDraft={() => {
            // 真实按钮 disabled={!draft}（CustomizePreviewDialog）保证点击
            // 时草稿已可见；快照取当前轮询帧的 html_hash。
            if (draft) auth.authorize(draft)
            // jsdom 无 scrollIntoView，守卫可选链（测试另桩断言）。
            rootRef.current?.scrollIntoView?.({ behavior: 'smooth' })
            setFlash(true)
          }}
          onClose={closeCustomizing}
        />
      )}
    </section>
  )
}
