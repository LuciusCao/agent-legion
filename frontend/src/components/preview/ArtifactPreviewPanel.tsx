/**
 * 通用产物预览面板：任何 job 的 artifacts 列表都能渲染（issue #11 的
 * 「什么都能看」层）。结构化业务面板（question 等）在其上方渲染，
 * 本面板兜底展示全部产物；面板头部的勾选菜单写 workspace 级
 * previewHidden 配置（取消勾选对该 workspace 的所有 job 生效）。
 */
import { useMemo } from 'react'
import { ArtifactPreviewCard } from './ArtifactPreviewCard'
import { ArtifactPreviewConfigMenu } from './ArtifactPreviewConfigMenu'
import { useWorkspacePreviewConfig } from '../../hooks/useWorkspacePreviewConfig'
import type { JobDetail } from '../../types/jobTypes'
import styles from './ArtifactPreviewPanel.module.css'

export interface ArtifactPreviewPanelProps {
  jobId: string
  detail: JobDetail | null
  /** workspace 级配置归属；缺省（无 workspace 上下文）时全部显示。 */
  workspaceId?: string
}

export function ArtifactPreviewPanel({
  jobId,
  detail,
  workspaceId,
}: ArtifactPreviewPanelProps) {
  const { previewHidden } = useWorkspacePreviewConfig(workspaceId)
  const artifacts = useMemo(() => detail?.artifacts ?? [], [detail?.artifacts])
  const hiddenSet = useMemo(() => new Set(previewHidden), [previewHidden])
  const visible = useMemo(
    () => artifacts.filter((name) => !hiddenSet.has(name)),
    [artifacts, hiddenSet]
  )

  return (
    <div className={styles.panel} data-testid="artifact-preview-panel">
      <header className={styles.header}>
        <h2 className={styles.title}>产物预览</h2>
        <span className={styles.count}>{visible.length} 个文件</span>
        <ArtifactPreviewConfigMenu workspaceId={workspaceId} artifacts={artifacts} />
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
