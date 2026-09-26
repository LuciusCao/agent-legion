/**
 * 「定制预览」覆盖面板的治理 footer（#615 方向 A 从对话框抽出，保体积预算）：
 * 草稿/已发布状态行 + 三个人工动作——「预览此草稿」（逐次授权，#347 P1）、
 * 「恢复默认」（归档，需确认）、「发布草稿」。发布/归档永远是人工动作，
 * agent 只写草稿（reject_studio_agent_scope 在后端钉死）。
 */
import { Button } from '@mui/material'
import type { PreviewPanelVersion } from './previewPanelApi'
import styles from './CustomizePreviewDialog.module.css'

export interface CustomizePreviewFooterProps {
  draft: PreviewPanelVersion | null
  published: PreviewPanelVersion | null
  /** 草稿预览是否已获逐次授权（与左栏渲染同一判定）。 */
  previewDraft: boolean
  publishing: boolean
  onPreviewDraft: () => void
  onPublish: () => void
  onArchive: () => void
}

export function CustomizePreviewFooter({
  draft,
  published,
  previewDraft,
  publishing,
  onPreviewDraft,
  onPublish,
  onArchive,
}: CustomizePreviewFooterProps) {
  return (
    <div className={styles.footer}>
      <span className={styles.footerStatus}>
        {draft ? `草稿 v${draft.version}（${draft.created_by}）` : '暂无草稿'}
        {' · '}
        {published
          ? `已发布 v${published.version}`
          : '未发布（当前为默认预览）'}
      </span>
      <Button
        size="small"
        variant={previewDraft ? 'contained' : 'outlined'}
        color={previewDraft ? 'warning' : 'primary'}
        disabled={!draft}
        onClick={onPreviewDraft}
      >
        {previewDraft ? '预览草稿中' : '预览此草稿'}
      </Button>
      <Button
        size="small"
        variant="outlined"
        disabled={!published && !draft}
        onClick={() => {
          if (window.confirm('恢复默认预览？已发布版本与草稿都会被归档。')) {
            onArchive()
          }
        }}
      >
        恢复默认
      </Button>
      <Button
        size="small"
        variant="contained"
        disabled={!draft || publishing}
        onClick={onPublish}
      >
        发布草稿
      </Button>
    </div>
  )
}
