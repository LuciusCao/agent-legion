import type { VideoArtifacts } from '../types'
import { useArtifactStore } from '../stores/artifactStore'

export function MetadataPanel({
  metadata: metadataProp,
}: {
  metadata?: VideoArtifacts['metadata']
}) {
  const { artifacts } = useArtifactStore()
  const meta = metadataProp ?? artifacts.metadata

  if (!meta) return <div className="empty-state">暂无元数据</div>

  return (
    <div className="tab-panel card-outlined">
      <pre>{JSON.stringify(meta, null, 2)}</pre>
    </div>
  )
}
