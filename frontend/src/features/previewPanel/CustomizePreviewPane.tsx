/**
 * 「定制预览」对话框的内嵌草稿预览区（issue #615）：预览渲染目标从
 * 「对话框外的左栏」扩展为「左栏 + 对话框内」双通道——模态对话框锁滚动
 * 且遮挡左栏，草稿渲染问题出现在面板首屏以下时，用户只能「关对话框 →
 * 滚动查看 → 再开对话框继续对话」，编排循环被打断，人工验证事实上不可用。
 * 内嵌预览与对话同屏，验证不再离开对话框。
 *
 * 授权语义不变（#347 P1 / #500）：本组件是**纯展示**——是否渲染草稿
 * iframe 完全由父级传入的 previewDraft（useDraftAuthorization 的逐次授权
 * 判定）唯一决定，这里不做也不重复任何授权判断；重开对话框/草稿内容
 * 变化/切 job/workspace 使授权失效时，previewDraft 翻 false、iframe 卸载，
 * 回到占位提示。草稿执行仍需显式点击，沙箱与 CSP 红线全部由
 * PreviewPanelHost 承担（这里是复用，不是第二套 iframe 实现）。
 */
import { PreviewPanelHost } from './PreviewPanelHost'
import { previewHostKey } from './bundleKey'
import type { PreviewPanelVersion } from './previewPanelApi'
import styles from './CustomizePreviewDialog.module.css'

export interface CustomizePreviewPaneProps {
  /** 桥上下文与重挂 key 的 job 身份（与左栏渲染同源）。 */
  jobId: string
  /** 治理面当前草稿（父级轮询帧）。 */
  draft: PreviewPanelVersion | null
  /** 父级的逐次授权判定：false 时只渲染占位，不挂草稿 iframe。 */
  previewDraft: boolean
}

export function CustomizePreviewPane({
  jobId,
  draft,
  previewDraft,
}: CustomizePreviewPaneProps) {
  return (
    <aside
      className={styles.previewPane}
      data-testid="customize-preview-pane"
      aria-label="草稿预览"
    >
      <div className={styles.previewHead}>
        草稿预览（仅本页可见
        {previewDraft ? ' · 左栏同步渲染全宽效果' : ''}）
      </div>
      <div className={styles.previewBody}>
        {previewDraft && draft !== null ? (
          <PreviewPanelHost
            key={previewHostKey(jobId, draft.html_hash)}
            jobId={jobId}
            html={draft.html}
            title="草稿预览（对话框内）"
          />
        ) : (
          <div className={styles.previewHint}>
            {draft
              ? `草稿 v${draft.version} 已就绪——点「预览此草稿」后在此渲染`
              : '暂无草稿：agent 保存草稿后即可在此预览'}
          </div>
        )}
      </div>
    </aside>
  )
}
