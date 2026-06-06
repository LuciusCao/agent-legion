import { useEffect } from 'react'
import { useParams, useNavigate, Routes, Route, useLocation } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import WorkspaceOverview from '../views/WorkspaceOverview'
import WorkspaceJobList from '../views/WorkspaceJobList'

const TABS = [
  { key: '', label: 'Overview' },
  { key: 'jobs', label: 'Jobs' },
  { key: 'dag', label: 'DAG' },
  { key: 'agents', label: 'Agents' },
  { key: 'runs', label: 'Runs' },
  { key: 'resources', label: 'Resources' },
]

const VIDEO_HIVE_TABS = [
  { key: '', label: '概览' },
  { key: 'jobs', label: '视频队列' },
  { key: 'agents', label: 'Agents' },
  { key: 'packages', label: 'Packages' },
]

export default function WorkspaceLayout() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const { workspaces, fetchWorkspaces, setCurrentWorkspace } = useWorkspaceStore()

  const isVideoHive = workspaceId === 'video-hive'
  const tabs = isVideoHive ? VIDEO_HIVE_TABS : TABS

  useEffect(() => {
    if (workspaces.length === 0) {
      fetchWorkspaces()
    }
  }, [workspaces.length, fetchWorkspaces])

  useEffect(() => {
    const ws = workspaces.find((w) => w.id === workspaceId)
    setCurrentWorkspace(ws || null)
  }, [workspaceId, workspaces, setCurrentWorkspace])

  // Determine current tab from path
  const pathParts = location.pathname.split('/')
  const currentTab = pathParts.length > 3 ? pathParts[3] : ''

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* Top bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          padding: '12px 24px',
          borderBottom: '1px solid var(--md-sys-color-outline-variant)',
          gap: 16,
          flexShrink: 0,
        }}
      >
        <md-icon-button onClick={() => navigate('/')}>
          <md-icon>arrow_back</md-icon>
        </md-icon-button>
        <span style={{ fontSize: 14, color: 'var(--md-sys-color-on-surface-variant)' }}>
          Agent Legion
        </span>
        <span style={{ color: 'var(--md-sys-color-outline)' }}>/</span>
        <h2 style={{ margin: 0, fontSize: 18 }}>
          {isVideoHive ? 'Video Hive' : workspaces.find((w) => w.id === workspaceId)?.name || workspaceId}
        </h2>
      </div>

      {/* Body */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Sidebar */}
        <div
          style={{
            width: 200,
            borderRight: '1px solid var(--md-sys-color-outline-variant)',
            padding: 8,
            overflowY: 'auto',
            flexShrink: 0,
          }}
        >
          <md-list>
            {tabs.map((tab) => {
              const isActive = currentTab === tab.key
              return (
                <md-list-item
                  key={tab.key}
                  type="button"
                  onClick={() => navigate(`/workspaces/${workspaceId}${tab.key ? '/' + tab.key : ''}`)}
                  style={{
                    background: isActive
                      ? 'var(--md-sys-color-secondary-container)'
                      : 'transparent',
                    borderRadius: 8,
                  }}
                >
                  <div slot="headline">{tab.label}</div>
                </md-list-item>
              )
            })}
          </md-list>
        </div>

        {/* Main content */}
        <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
          <Routes>
            <Route path="/" element={<WorkspaceOverview isVideoHive={isVideoHive} />} />
            <Route path="/jobs" element={<WorkspaceJobList isVideoHive={isVideoHive} />} />
            <Route
              path="/agents"
              element={
                <div style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
                  Agents view — 待实现
                </div>
              }
            />
            <Route
              path="/dag"
              element={
                <div style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>DAG view — 待实现</div>
              }
            />
            <Route
              path="/runs"
              element={
                <div style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>Runs view — 待实现</div>
              }
            />
            <Route
              path="/resources"
              element={
                <div style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
                  Resources view — 待实现
                </div>
              }
            />
            <Route
              path="/packages"
              element={
                <div style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
                  Packages view — 待实现
                </div>
              }
            />
          </Routes>
        </div>
      </div>
    </div>
  )
}
