/**
 * PreviewPanelSection 的头部（#528 / #796 验收返工）：纯展示组件。
 * - 标题行：「内容预览」+ 草稿预览中徽标 + #528 模式开关（定制面板 |
 *   原始界面，bundle 存在才渲染、非 admin 可用）+ 「定制预览」入口
 *   （admin-only）。
 * - 治理行（admin-only，有草稿或已发布时）：草稿/已发布状态行 + 三个
 *   人工动作——「预览此草稿」（逐次授权，#347 P1）、「发布草稿」、
 *   「恢复默认」（归档，需确认）。动作语义与回调全部来自父级，这里不做
 *   任何授权/治理判断。
 */
import { Button } from '@mui/material'
import type { PreviewPanelVersion } from './previewPanelApi'
import type { PreviewDisplayMode } from './previewDisplayMode'
import styles from './PreviewPanelHeader.module.css'

export interface PreviewPanelHeaderProps {
  isAdmin: boolean
  /** 草稿预览态（授权生效中）：控制徽标与「预览草稿中」按钮态。 */
  draftPreview: boolean
  draft: PreviewPanelVersion | null
  published: PreviewPanelVersion | null
  /** #528：是否渲染模式开关（已发布 bundle 存在时）。 */
  showModeToggle: boolean
  mode: PreviewDisplayMode
  onSelectMode: (mode: PreviewDisplayMode) => void
  publishing: boolean
  actionError: string | null
  onPreviewDraft: () => void
  onPublish: () => void
  onArchive: () => void
  onCustomize: () => void
}

export function PreviewPanelHeader({
  isAdmin,
  draftPreview,
  draft,
  published,
  showModeToggle,
  mode,
  onSelectMode,
  publishing,
  actionError,
  onPreviewDraft,
  onPublish,
  onArchive,
  onCustomize,
}: PreviewPanelHeaderProps) {
  return (
    <header className={styles.header}>
      <div className={styles.titleRow}>
        <h2 className={styles.title}>内容预览</h2>
        {draftPreview && <span className={styles.draftBadge}>草稿预览中</span>}
        <span className={styles.spacer} />
        {showModeToggle && (
          <div
            className={styles.modeToggle}
            role="group"
            aria-label="预览显示模式"
          >
            <button
              type="button"
              aria-pressed={mode === 'custom'}
              className={
                mode === 'custom'
                  ? `${styles.modeButton} ${styles.modeButtonActive}`
                  : styles.modeButton
              }
              onClick={() => onSelectMode('custom')}
            >
              定制面板
            </button>
            <button
              type="button"
              aria-pressed={mode === 'original'}
              className={
                mode === 'original'
                  ? `${styles.modeButton} ${styles.modeButtonActive}`
                  : styles.modeButton
              }
              onClick={() => onSelectMode('original')}
            >
              原始界面
            </button>
          </div>
        )}
        {isAdmin && (
          <button
            type="button"
            className={styles.customizeButton}
            onClick={onCustomize}
          >
            定制预览
          </button>
        )}
      </div>
      {isAdmin && (draft !== null || published !== null) && (
        <div className={styles.govRow}>
          <span className={styles.govStatus}>
            {draft
              ? `草稿 v${draft.version}（${draft.created_by}）`
              : '暂无草稿'}
            {' · '}
            {published
              ? `已发布 v${published.version}`
              : '未发布（当前为默认预览）'}
          </span>
          <Button
            size="small"
            variant={draftPreview ? 'contained' : 'outlined'}
            color={draftPreview ? 'warning' : 'primary'}
            disabled={!draft}
            onClick={onPreviewDraft}
          >
            {draftPreview ? '预览草稿中' : '预览此草稿'}
          </Button>
          <Button
            size="small"
            variant="contained"
            disabled={!draft || publishing}
            onClick={onPublish}
          >
            发布草稿
          </Button>
          <Button
            size="small"
            variant="outlined"
            disabled={!published && !draft}
            onClick={() => {
              if (
                window.confirm('恢复默认预览？已发布版本与草稿都会被归档。')
              ) {
                onArchive()
              }
            }}
          >
            恢复默认
          </Button>
          {actionError && (
            <span className={styles.govError} role="alert">
              {actionError}
            </span>
          )}
        </div>
      )}
    </header>
  )
}
