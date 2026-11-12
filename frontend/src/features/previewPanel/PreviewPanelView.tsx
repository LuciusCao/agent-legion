import type { Dispatch, ReactNode, SetStateAction } from 'react'
import { PreviewPanelHost } from './PreviewPanelHost'
import { previewHostKey } from './bundleKey'
import { CustomizePreviewDock } from './CustomizePreviewDock'
import { PreviewPanelHeader } from './PreviewPanelHeader'
import type { PreviewDisplayMode } from './previewDisplayMode'
import type { PreviewPanelVersion } from './previewPanelApi'
import type { DraftAuthorizationApi } from './useDraftAuthorization'
import type { usePreviewGovernance } from './usePreviewGovernance'
import styles from './PreviewPanelSection.module.css'

interface PreviewPanelViewProps {
  jobId: string
  workspaceId?: string
  fallback: ReactNode
  isAdmin: boolean
  customizing: boolean
  setCustomizing: Dispatch<SetStateAction<boolean>>
  draftPreview: boolean
  draft: PreviewPanelVersion | null
  published: PreviewPanelVersion | null
  mode: PreviewDisplayMode
  onSelectMode: (next: PreviewDisplayMode) => void
  governance: ReturnType<typeof usePreviewGovernance>
  auth: DraftAuthorizationApi
  showCustomBundle: boolean
  bundleVersion: PreviewPanelVersion | null
}

export function PreviewPanelView({
  jobId,
  workspaceId,
  fallback,
  isAdmin,
  customizing,
  setCustomizing,
  draftPreview,
  draft,
  published,
  mode,
  onSelectMode,
  governance,
  auth,
  showCustomBundle,
  bundleVersion,
}: PreviewPanelViewProps) {
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
          onSelectMode={onSelectMode}
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
