/**
 * 通用产物预览面板（issue #11「什么都能看」层）：结构化业务面板在上方，
 * 本面板兜底全部产物。#255 起两项默认：
 * - 默认折叠为一行摘要（方案 A）：原始文件是次要/兜底视图，不与结构化
 *   面板抢首屏；非 question 实体也获得一致的次级呈现。点击头部展开。
 * - 结构化面板消费的产物默认去重（方案 B）：structuredHidden 名单内的
 *   文件已以业务形态在上方展示，原始卡片默认隐藏；勾选菜单可会话内
 *   恢复。可见性派生与会话态恢复在 useArtifactVisibility。
 * 无名单的实体行为与 #248 一致（全部默认显示，仅多一层折叠）。
 */
import { useState } from 'react'
import { ArtifactPreviewCard } from './ArtifactPreviewCard'
import { ArtifactPreviewConfigMenu } from './ArtifactPreviewConfigMenu'
import { useArtifactVisibility } from '../../hooks/useArtifactVisibility'
import { MaterialIcon } from '../MaterialIcon'
import type { JobDetail } from '../../types/jobTypes'
import styles from './ArtifactPreviewPanel.module.css'

/** 空名单模块级常量：默认参数不能每次 render 生成新引用。 */
const NO_STRUCTURED_HIDDEN: readonly string[] = []

export interface ArtifactPreviewPanelProps {
  jobId: string
  detail: JobDetail | null
  /** workspace 级配置归属；缺省（无 workspace 上下文）时全部显示。 */
  workspaceId?: string
  /** 结构化面板已消费、默认不在本面板重复展示的产物名。 */
  structuredHidden?: readonly string[]
}

export function ArtifactPreviewPanel({
  jobId,
  detail,
  workspaceId,
  structuredHidden = NO_STRUCTURED_HIDDEN,
}: ArtifactPreviewPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const artifacts = detail?.artifacts ?? []
  const { hiddenNames, visible, dedupedCount, toggleVisibility } =
    useArtifactVisibility(artifacts, workspaceId, structuredHidden)

  return (
    <div className={styles.panel} data-testid="artifact-preview-panel">
      <header className={styles.header}>
        <h2 className={styles.title}>
          <button
            type="button"
            className={styles.toggleButton}
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            <MaterialIcon
              name={expanded ? 'expand_less' : 'expand_more'}
              fontSize="small"
            />
            产物预览
          </button>
        </h2>
        <span className={styles.count}>{visible.length} 个文件</span>
        {dedupedCount > 0 && (
          <span className={styles.count}>另 {dedupedCount} 个已在上方展示</span>
        )}
        <ArtifactPreviewConfigMenu
          artifacts={artifacts}
          hiddenNames={hiddenNames}
          onToggle={toggleVisibility}
        />
      </header>
      {expanded &&
        (visible.length === 0 ? (
          <p className={styles.empty}>
            {dedupedCount > 0 ? '其余产物已在上方展示' : '暂无产物文件'}
          </p>
        ) : (
          visible.map((name) => (
            <ArtifactPreviewCard
              key={name}
              jobId={jobId}
              name={name}
              detail={detail}
            />
          ))
        ))}
    </div>
  )
}
