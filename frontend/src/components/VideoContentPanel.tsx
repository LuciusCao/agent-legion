import styles from './VideoContentPanel.module.css'

export interface VideoContentPanelProps {
  jobId: string
}

/**
 * Transitional shell (issue #11): the dedicated `/api/jobs/{id}/video`
 * detail endpoint retired with the business workflow extraction. The panel
 * stays registered as the video entity renderer and will be rewired to the
 * generic preview manifest when the capability-declared preview lands;
 * until then it renders the empty state.
 */
export function VideoContentPanel({ jobId }: VideoContentPanelProps) {
  void jobId
  return (
    <div className={styles.panel} data-testid="video-content-panel">
      <p className={styles.empty}>视频内容尚未生成</p>
    </div>
  )
}
