/**
 * 左栏内容预览分区（issue #328 / #528 / #796 返工）：
 * - workspace 有已发布预览面板 bundle → 沙箱 iframe 渲染它（整栏接管）；
 *   无 → 渲染 fallback（question 的内置 bundle / 通用产物预览，由调用方组装）；
 * - #528 预览开关：已发布即永久接管没有退出路径，「恢复默认」是归档治理
 *   动作不是「暂时不看了」。头部加「定制面板 | 原始界面」开关——纯客户端
 *   查看偏好（previewDisplayMode 按 workspace 存 localStorage，默认定制
 *   面板），关闭后回落 fallback；非 admin 可用（查看偏好非治理动作）；
 *   draftPreview 草稿预览态优先级高于开关，不被拦断。
 * - 草稿**不自动执行**（#347 P1）：agent（或提示注入产物）写入的 HTML 只有
 *   在当前用户显式点「预览此草稿」后才作为 srcDoc 挂载——点击预览是逐次
 *   授权：关面板、草稿消失（发布/归档的 null 过渡）、切换 job/workspace、
 *   草稿内容变化（save_draft 覆盖）四者都使授权失效，新草稿/新上下文不
 *   继承旧授权（避免一次点击永久放行）。授权的快照与 render 期派生比对抽
 *   在 useDraftAuthorization（#500 P1-3/P1-5）；发布永远是人工动作。
 * - #796 返工：治理动作（预览此草稿/发布草稿/恢复默认）与草稿状态行从
 *   Dock footer 迁到本区头部（PreviewPanelHeader）；Dock 收敛为纯对话
 *   （AgentPanelDock + AgentChatPanel，见 CustomizePreviewDock）。草稿的
 *   渲染目标是本区既有 PreviewPanelHost（与已发布版本同一挂载点、同一
 *   draftPreview 判定）——「预览此草稿」在 Dock 未开时会同时唤起 Dock，
 *   授权仍锚定 Dock 会话（#347 P1 语义不变）。
 * 定制入口与治理行 admin-only（与 WorkspaceMoreMenu 的 Studio 项同一惯例，
 * P4/STUDIO-AGENT-001：治理面端点本身 admin/scoped-only，非 admin 点开只会
 * 收获一串 403）；#528 开关不受此限。
 */
import { useState, type ReactNode } from 'react'
import { useAuthStore } from '../../stores/authStore'
import { PreviewPanelHost } from './PreviewPanelHost'
import { previewHostKey } from './bundleKey'
import { CustomizePreviewDock } from './CustomizePreviewDock'
import { PreviewPanelHeader } from './PreviewPanelHeader'
import {
  savePreviewDisplayMode,
  resolvePreviewDisplayMode,
  type PreviewDisplayMode,
  type PreviewDisplayModeOverride,
} from './previewDisplayMode'
import {
  usePreviewPanelState,
  usePublishedPreviewPanel,
} from './usePreviewPanel'
import { usePreviewGovernance } from './usePreviewGovernance'
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
  // 治理面状态对 admin 常驻轮询（头部治理行需要草稿状态；原来只在面板
  // 打开时启用）；非 admin 永远不发 403 轮询。
  const stateQuery = usePreviewPanelState(workspaceId, isAdmin)
  const governance = usePreviewGovernance(workspaceId)
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

  // #528：查看偏好。override 记录「本次会话内手动切换的 workspace 与
  // 模式」；override 与当前 workspace 不符（含未切换过）时现读该
  // workspace 的存储偏好——react-router 复用组件实例跨 workspace 导航时
  // 不会把 ws1 的会话内覆盖串到 ws2（解析规则抽在 previewDisplayMode）。
  const [modeOverride, setModeOverride] =
    useState<PreviewDisplayModeOverride | null>(null)
  const mode = resolvePreviewDisplayMode(workspaceId, modeOverride)
  // draftPreview 优先于开关（issue #528 验收：授权预览草稿不被开关拦断）。
  const showCustomBundle = draftPreview || mode === 'custom'

  function selectMode(next: PreviewDisplayMode) {
    if (!workspaceId) return
    setModeOverride({ workspaceId, mode: next })
    savePreviewDisplayMode(workspaceId, next)
  }

  return (
    <section className={styles.root} data-testid="preview-panel-section">
      {workspaceId && (
        <PreviewPanelHeader
          isAdmin={isAdmin}
          draftPreview={draftPreview}
          draft={draft}
          published={published}
          showModeToggle={Boolean(published?.html)}
          mode={mode}
          onSelectMode={selectMode}
          publishing={governance.publishing}
          actionError={governance.actionError}
          onPreviewDraft={() => {
            // 按钮 disabled={!draft}（PreviewPanelHeader）保证点击时草稿
            // 已可见；快照取当前轮询帧的 html_hash。授权锚定 Dock 会话：
            // Dock 未开时先唤起（关 Dock 授权即失效，#347 P1 语义不变）。
            if (draft) {
              setCustomizing(true)
              auth.authorize(draft)
            }
          }}
          onPublish={() => {
            // #841：CAS 令牌取头部展示的同一草稿帧（按钮 disabled={!draft}
            // 保证可见）；授权预览中时它即被预览的那份（授权按 html_hash
            // 派生比对，见 useDraftAuthorization）。草稿在点击前被覆盖 →
            // 服务端 409，不会发出人没看过的内容。
            if (draft) governance.publish(draft.html_hash)
          }}
          onArchive={governance.archive}
          onCustomize={() => setCustomizing(true)}
        />
      )}
      {showCustomBundle && bundleVersion?.html ? (
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
      {/* CustomizePreviewDock 关闭即卸载（composer 文本/队列随之丢弃）是
          有意取舍（#797 复审批次确认）：#347 P1 语义锚定「关 Dock = 授权
          失效」，与 studio chat Dock 的「隐藏不卸载」是两种不同契约。 */}
      {customizing && isAdmin && workspaceId && (
        <CustomizePreviewDock
          workspaceId={workspaceId}
          onClose={() => setCustomizing(false)}
        />
      )}
    </section>
  )
}
