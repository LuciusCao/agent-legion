/**
 * 通用产物预览面板：任何 job 的 artifacts 列表都能渲染（issue #11 的
 * 「什么都能看」层）。结构化业务面板（question 等）在其上方渲染，
 * 本面板兜底展示全部产物；workspace 级 previewHidden 勾选配置在
 * 面板头部的菜单里（useWorkspacePreviewConfig 接入后生效）。
 */
import { useMemo } from 'react'
import { ArtifactPreviewCard } from './ArtifactPreviewCard'
import type { JobDetail } from '../../types/jobTypes'
import styles from './ArtifactPreviewPanel.module.css'

export interface ArtifactPreviewPanelProps {
  jobId: string
  detail: JobDetail | null
  /** workspace 级隐藏列表（Phase 6 接入，此前恒为空 = 全部显示）。 */
  hiddenArtifacts?: string[]
}

export function ArtifactPreviewPanel({
  jobId,
  detail,
  hiddenArtifacts = [],
}: ArtifactPreviewPanelProps) {
  const visible = useMemo(() => {
    const hidden = new Set(hiddenArtifacts)
    return (detail?.artifacts ?? []).filter((name) => !hidden.has(name))
  }, [detail?.artifacts, hiddenArtifacts])

  return (
    <div className={styles.panel} data-testid="artifact-preview-panel">
      <header className={styles.header}>
        <h2 className={styles.title}>产物预览</h2>
        <span className={styles.count}>{visible.length} 个文件</span>
      </header>
      {visible.length === 0 ? (
        <p className={styles.empty}>暂无产物文件</p>
      ) : (
        visible.map((name) => (
          <ArtifactPreviewCard key={name} jobId={jobId} name={name} detail={detail} />
        ))
      )}
    </div>
  )
}
