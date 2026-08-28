/**
 * 单个产物预览卡片：标题行（文件名 + 类型徽标 + raw 链接）+ 渲染器。
 * 卡片自身不做分类判断——kind 由面板计算后传入。
 */
import { createElement } from 'react'
import { classifyArtifactPreview, PREVIEW_KIND_LABELS } from '../../lib/previewKind'
import { resolvePreviewRenderer } from './previewRegistry'
import type { JobDetail } from '../../types/jobTypes'
import styles from './ArtifactPreviewCard.module.css'

export interface ArtifactPreviewCardProps {
  jobId: string
  name: string
  detail: JobDetail | null
}

export function ArtifactPreviewCard({ jobId, name, detail }: ArtifactPreviewCardProps) {
  const kind = classifyArtifactPreview(name)
  const Renderer = resolvePreviewRenderer(kind)
  return (
    <section className={styles.card} data-testid="artifact-preview-card">
      <header className={styles.header}>
        <h2 className={styles.title}>{name}</h2>
        <span className={styles.kindBadge}>{PREVIEW_KIND_LABELS[kind]}</span>
      </header>
      <div className={styles.body}>
        {createElement(Renderer, { jobId, name, detail })}
      </div>
    </section>
  )
}
