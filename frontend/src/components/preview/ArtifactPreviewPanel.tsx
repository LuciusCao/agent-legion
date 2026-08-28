/**
 * 通用产物预览面板（issue #11「什么都能看」层）：结构化业务面板在上方，
 * 本面板兜底全部产物；头部勾选菜单写 workspace 级 previewHidden。
 */
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
  const { visibleArtifacts } = useWorkspacePreviewConfig(workspaceId)
  const artifacts = detail?.artifacts ?? []
  const visible = visibleArtifacts(artifacts)

  return (
    <div className={styles.panel} data-testid="artifact-preview-panel">
      <header className={styles.header}>
        <h2 className={styles.title}>产物预览</h2>
        <span className={styles.count}>{visible.length} 个文件</span>
        <ArtifactPreviewConfigMenu
          workspaceId={workspaceId}
          artifacts={artifacts}
        />
      </header>
      {visible.length === 0 ? (
        <p className={styles.empty}>暂无产物文件</p>
      ) : (
        visible.map((name) => (
          <ArtifactPreviewCard
            key={name}
            jobId={jobId}
            name={name}
            detail={detail}
          />
        ))
      )}
    </div>
  )
}
