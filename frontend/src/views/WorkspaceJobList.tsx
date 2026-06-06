import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useVideoStore } from '../stores/videoStore'
import { useVideoEvents } from '../hooks/useVideoEvents'

type Props = {
  isVideoHive: boolean
}

export default function WorkspaceJobList({ isVideoHive }: Props) {
  const navigate = useNavigate()
  const { currentWorkspace } = useWorkspaceStore()
  const { videos, fetchVideos } = useVideoStore()

  if (isVideoHive) {
    useVideoEvents()
  }

  useEffect(() => {
    if (isVideoHive) {
      fetchVideos()
    }
  }, [isVideoHive, fetchVideos])

  if (isVideoHive) {
    return (
      <div>
        <h3>视频队列</h3>
        {videos.length === 0 ? (
          <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>暂无视频</p>
        ) : (
          <md-list>
            {videos.map((video) => (
              <md-list-item
                key={video.id}
                type="button"
                onClick={() => navigate(`/videos/${video.id}`)}
              >
                <div slot="headline">{video.title || video.external_id}</div>
                <div slot="supporting-text">
                  {video.status} · {video.content_type}
                </div>
              </md-list-item>
            ))}
          </md-list>
        )}
      </div>
    )
  }

  return (
    <div>
      <h3>{currentWorkspace?.name || 'Workspace'} Jobs</h3>
      <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
        Agent Legion job 列表将在此展示。
      </p>
    </div>
  )
}
