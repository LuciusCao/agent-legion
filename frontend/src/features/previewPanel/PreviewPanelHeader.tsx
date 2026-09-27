/**
 * PreviewPanelSection 的头部（#528 / #796 验收返工 R2→R3→R4）：纯展示组件，
 * 单行克制排布（flex-wrap 兜底窄栏）。
 * - 标题「内容预览」+ 草稿预览中徽标；
 * - 治理区（admin 且有草稿/已发布）：状态 Chip（「草稿 v2 · 未发布」）+
 *   紧跟的两个外露小按钮「预览此草稿 / 发布草稿」（状态 + 可对它做的事
 *   一组，#796 R4——不收进溢出菜单）；
 * - #528 模式开关（定制面板 | 原始界面，bundle 存在才渲染、非 admin 可用）；
 * - 「定制预览」主操作按钮（admin-only）；
 * - ⋮ 溢出菜单只剩「恢复默认」（归档破坏性动作收拢，danger 色 + 确认，
 *   PreviewGovernanceMenu）；
 * - actionError 为行内紧凑红字（wrap 到下一行，不撑开行高）。
 * 动作语义与回调全部来自父级，这里不做任何授权/治理判断。
 */
import { Button, Chip } from '@mui/material'
import type { PreviewPanelVersion } from './previewPanelApi'
import type { PreviewDisplayMode } from './previewDisplayMode'
import { PreviewGovernanceMenu } from './PreviewGovernanceMenu'
import styles from './PreviewPanelHeader.module.css'

export interface PreviewPanelHeaderProps {
  isAdmin: boolean
  /** 草稿预览态（授权生效中）：控制徽标与「预览此草稿」菜单项态。 */
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

/** #528 分段开关的单段：激活段深色（hover 一并钉住，见 css 注释）。 */
function ModeButton(props: {
  active: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-pressed={props.active}
      className={
        props.active
          ? `${styles.modeButton} ${styles.modeButtonActive}`
          : styles.modeButton
      }
      onClick={props.onClick}
    >
      {props.label}
    </button>
  )
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
  const showGovernance = isAdmin && (draft !== null || published !== null)
  return (
    <header className={styles.header}>
      <h2 className={styles.title}>内容预览</h2>
      {draftPreview && <span className={styles.draftBadge}>草稿预览中</span>}
      {showGovernance && (
        <Chip
          size="small"
          variant="outlined"
          label={`${draft ? `草稿 v${draft.version}` : '暂无草稿'} · ${
            published ? `已发布 v${published.version}` : '未发布'
          }`}
        />
      )}
      {showGovernance && (
        <Button
          size="small"
          variant={draftPreview ? 'contained' : 'outlined'}
          color={draftPreview ? 'warning' : 'primary'}
          disabled={!draft || draftPreview}
          onClick={onPreviewDraft}
        >
          {draftPreview ? '预览草稿中' : '预览此草稿'}
        </Button>
      )}
      {showGovernance && (
        <Button
          size="small"
          variant="outlined"
          disabled={!draft || publishing}
          onClick={onPublish}
        >
          发布草稿
        </Button>
      )}
      <span className={styles.spacer} />
      {showModeToggle && (
        <div
          className={styles.modeToggle}
          role="group"
          aria-label="预览显示模式"
        >
          <ModeButton
            active={mode === 'custom'}
            label="定制面板"
            onClick={() => onSelectMode('custom')}
          />
          <ModeButton
            active={mode === 'original'}
            label="原始界面"
            onClick={() => onSelectMode('original')}
          />
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
      {showGovernance && (
        <PreviewGovernanceMenu
          canArchive={published !== null || draft !== null}
          onArchive={onArchive}
        />
      )}
      {actionError && (
        <span className={styles.govError} role="alert">
          {actionError}
        </span>
      )}
    </header>
  )
}
