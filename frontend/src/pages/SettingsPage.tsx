import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useSettingStore } from '../stores/settingStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { AgentAllocationList } from '../components/AgentAllocationList'
import { SettingsCard } from '../components/SettingsCard'
import { WORKSPACE_LABELS } from '../labels'
import type { GlobalServiceStatus } from '../types'

const PIPELINE_OPTIONS = [
  { key: 'question_content', label: 'question_content' },
  { key: 'knowledge_content', label: 'knowledge_content' },
]

type ConnectionState = 'idle' | 'testing' | 'success' | 'failed'

function ConnectionStatusPill({
  state,
  message,
}: {
  state: ConnectionState
  message?: string
}) {
  if (state === 'idle') return null
  const labels: Record<ConnectionState, string> = {
    idle: '',
    testing: '测试中...',
    success: '连接成功',
    failed: '连接失败',
  }
  const className = `status-badge ${state === 'testing' ? 'running' : state}`
  return (
    <span className={className}>
      {labels[state]}
      {message ? ` · ${message}` : ''}
    </span>
  )
}

function GlobalServicesCard({
  services,
}: {
  services: GlobalServiceStatus | null
}) {
  if (!services) return null
  const { cms } = services
  return (
    <SettingsCard icon="cloud" title={WORKSPACE_LABELS.globalServices}>
      <div style={{ display: 'grid', gap: 12 }}>
        <div>
          <span
            style={{
              fontSize: 12,
              color: 'var(--md-sys-color-on-surface-variant)',
            }}
          >
            {WORKSPACE_LABELS.globalUrl}
          </span>
          <div style={{ fontSize: 14, marginTop: 4 }}>{cms.baseUrl}</div>
        </div>
        <div>
          <span
            style={{
              fontSize: 12,
              color: 'var(--md-sys-color-on-surface-variant)',
            }}
          >
            {WORKSPACE_LABELS.tokenStatus}
          </span>
          <div style={{ fontSize: 14, marginTop: 4 }}>
            {cms.tokenConfigured
              ? WORKSPACE_LABELS.tokenConfigured
              : WORKSPACE_LABELS.tokenNotConfigured}
          </div>
        </div>
        <div>
          <span
            style={{
              fontSize: 12,
              color: 'var(--md-sys-color-on-surface-variant)',
            }}
          >
            {WORKSPACE_LABELS.env}
          </span>
          <div style={{ fontSize: 14, marginTop: 4 }}>{cms.env || '-'}</div>
        </div>
      </div>
    </SettingsCard>
  )
}

export function SettingsPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const {
    setWorkspaceId,
    settings,
    setSettings,
    globalServices,
    resourceProviders,
    pipelineDefinition,
    testStatus,
    isSaving,
    saveError,
    testConnection,
    resetTestStatus,
    saveSection,
    saveIntakeConfig,
    fetchSettings,
    fetchGlobalServices,
    fetchResourceProviders,
    fetchPipelineDefinition,
  } = useSettingStore()

  const { workspaces, fetchWorkspaces, updateWorkspace } = useWorkspaceStore()

  const [workspaceEditName, setWorkspaceEditName] = useState('')
  const [workspaceEditDescription, setWorkspaceEditDescription] = useState('')
  const [isSavingWorkspace, setIsSavingWorkspace] = useState(false)
  const [workspaceSaveError, setWorkspaceSaveError] = useState<string | null>(
    null
  )

  useEffect(() => {
    if (!workspaceId) return
    setWorkspaceId(workspaceId)
    resetTestStatus()
    void fetchSettings(workspaceId).then(() => {
      void fetchPipelineDefinition()
    })
  }, [
    workspaceId,
    setWorkspaceId,
    resetTestStatus,
    fetchSettings,
    fetchPipelineDefinition,
  ])

  useEffect(() => {
    if (!workspaceId) return
    void fetchGlobalServices()
    void fetchResourceProviders()
  }, [workspaceId, fetchGlobalServices, fetchResourceProviders])

  useEffect(() => {
    if (workspaces.length === 0) {
      fetchWorkspaces()
    }
  }, [workspaces.length, fetchWorkspaces])

  const workspace = workspaceId
    ? workspaces.find((w) => w.id === workspaceId)
    : undefined
  const workspaceName = workspace?.name || workspaceId || ''

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (workspace) {
      setWorkspaceEditName(workspace.name)
      setWorkspaceEditDescription(workspace.description || '')
    }
  }, [workspace])
  /* eslint-enable react-hooks/set-state-in-effect */

  const isTesting = testStatus.state === 'testing'

  const toggleIntakeMode = (key: string) => {
    const isEnabled = settings.intakeModes.includes(key)
    const nextModes = isEnabled
      ? settings.intakeModes.filter((k) => k !== key)
      : [...settings.intakeModes, key]

    const mode = pipelineDefinition?.intake?.modes.find((m) => m.key === key)
    if (mode?.resource) {
      const binding = settings.resources[mode.resource] || {
        enabled: true,
        config: {},
      }
      const nextResources = {
        ...settings.resources,
        [mode.resource]: { ...binding, enabled: !isEnabled },
      }
      setSettings({ intakeModes: nextModes, resources: nextResources })
    } else {
      setSettings({ intakeModes: nextModes })
    }
  }

  if (!workspaceId) return null

  const handleSaveWorkspaceInfo = async () => {
    if (!workspaceId) return
    setIsSavingWorkspace(true)
    setWorkspaceSaveError(null)
    try {
      await updateWorkspace(workspaceId, {
        name: workspaceEditName,
        description: workspaceEditDescription,
      })
    } catch (err) {
      setWorkspaceSaveError(String(err))
    } finally {
      setIsSavingWorkspace(false)
    }
  }

  const connectionStatus = (
    <ConnectionStatusPill
      state={testStatus.state}
      message={testStatus.message}
    />
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* App bar */}
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '12px 24px',
          borderBottom: '1px solid var(--md-sys-color-outline-variant)',
          flexShrink: 0,
        }}
      >
        <md-icon-button onClick={() => navigate(`/workspaces/${workspaceId}`)}>
          <md-icon>arrow_back</md-icon>
        </md-icon-button>
        <h1
          style={{
            margin: 0,
            fontSize: 20,
            fontWeight: 500,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {workspaceName} / 设置
        </h1>
      </header>

      {/* Main content */}
      <main style={{ flex: 1, overflow: 'auto', padding: '24px 24px 32px' }}>
        <div style={{ maxWidth: 800, margin: '0 auto' }}>
          <SettingsCard icon="info" title="基本信息">
            <md-outlined-text-field
              label="Workspace 名称"
              value={workspaceEditName}
              onInput={(event: Event) =>
                setWorkspaceEditName((event.target as HTMLInputElement).value)
              }
              style={{ width: '100%' }}
            />
            <md-outlined-text-field
              label="描述"
              type="textarea"
              rows={2}
              value={workspaceEditDescription}
              onInput={(event: Event) =>
                setWorkspaceEditDescription(
                  (event.target as HTMLInputElement).value
                )
              }
              style={{ width: '100%' }}
            />
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'flex-end',
                gap: 8,
              }}
            >
              <md-filled-button
                onClick={handleSaveWorkspaceInfo}
                disabled={isSavingWorkspace || undefined}
              >
                保存
              </md-filled-button>
              {workspaceSaveError && (
                <div
                  className="error-text"
                  role="alert"
                  style={{ color: 'var(--md-sys-color-error)' }}
                >
                  {workspaceSaveError}
                </div>
              )}
            </div>
          </SettingsCard>

          <GlobalServicesCard services={globalServices} />

          <SettingsCard
            icon="input"
            title="接入配置"
            status={
              <div aria-live="polite" aria-atomic="true">
                {connectionStatus}
              </div>
            }
          >
            <div className="field">
              <label htmlFor="entity-type">默认实体类型</label>
              <select
                id="entity-type"
                value={settings.entityType}
                onChange={(e) =>
                  setSettings({
                    entityType: e.target.value as
                      | 'question'
                      | 'knowledge'
                      | 'video',
                  })
                }
              >
                <option value="question">question</option>
                <option value="knowledge">knowledge</option>
                <option value="video">video</option>
              </select>
            </div>

            <div>
              <span
                style={{
                  fontSize: 12,
                  color: 'var(--md-sys-color-on-surface-variant)',
                }}
              >
                接入模式
              </span>
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 8,
                  marginTop: 8,
                }}
              >
                {(pipelineDefinition?.intake?.modes || []).map((mode) => (
                  <div
                    key={mode.key}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                    }}
                  >
                    <md-checkbox
                      checked={settings.intakeModes.includes(mode.key)}
                      onClick={() => toggleIntakeMode(mode.key)}
                    />
                    <span style={{ fontSize: 14 }}>{mode.label}</span>
                  </div>
                ))}
              </div>
            </div>

            {(() => {
              const activeKeys = new Set<string>()
              for (const mode of pipelineDefinition?.intake?.modes || []) {
                if (settings.intakeModes.includes(mode.key) && mode.resource) {
                  activeKeys.add(mode.resource)
                }
              }
              if (activeKeys.size === 0) return null
              return (
                <div
                  style={{ display: 'flex', flexDirection: 'column', gap: 16 }}
                >
                  <div
                    style={{
                      fontSize: 12,
                      color: 'var(--md-sys-color-on-surface-variant)',
                      marginTop: 8,
                    }}
                  >
                    资源接口参数
                  </div>
                  {resourceProviders
                    .filter((p) => activeKeys.has(p.key))
                    .map((provider) => {
                      const binding = settings.resources[provider.key] || {
                        enabled: true,
                        config: {},
                      }
                      return (
                        <div
                          key={provider.key}
                          style={{
                            border:
                              '1px solid var(--md-sys-color-outline-variant)',
                            borderRadius: 12,
                            padding: 16,
                          }}
                        >
                          <div
                            style={{
                              fontWeight: 500,
                              fontSize: 14,
                              marginBottom: 4,
                            }}
                          >
                            {provider.provider}
                          </div>
                          <div
                            style={{
                              fontSize: 12,
                              color: 'var(--md-sys-color-on-surface-variant)',
                              marginBottom: 12,
                            }}
                          >
                            Path: {provider.path}
                          </div>
                          <div style={{ display: 'grid', gap: 8 }}>
                            {provider.paramKeys.map((paramKey) => (
                              <md-outlined-text-field
                                key={paramKey}
                                label={paramKey}
                                placeholder={
                                  provider.defaultParams[paramKey] || ''
                                }
                                value={binding.config[paramKey] || ''}
                                onInput={(event: Event) => {
                                  const value = (
                                    event.target as HTMLInputElement
                                  ).value
                                  const nextConfig = { ...binding.config }
                                  if (value) {
                                    nextConfig[paramKey] = value
                                  } else {
                                    delete nextConfig[paramKey]
                                  }
                                  setSettings({
                                    resources: {
                                      ...settings.resources,
                                      [provider.key]: {
                                        ...binding,
                                        config: nextConfig,
                                      },
                                    },
                                  })
                                }}
                                style={{ width: '100%' }}
                              />
                            ))}
                          </div>
                        </div>
                      )
                    })}
                </div>
              )
            })()}

            <div
              style={{
                display: 'flex',
                gap: 12,
                flexWrap: 'wrap',
                marginTop: 16,
              }}
            >
              <md-outlined-button
                onClick={testConnection}
                disabled={isTesting || isSaving || undefined}
              >
                {isTesting ? '测试中...' : '测试连接'}
              </md-outlined-button>
              <md-filled-button
                onClick={() => void saveIntakeConfig()}
                disabled={isSaving || undefined}
              >
                保存
              </md-filled-button>
            </div>
            {saveError && (
              <div
                className="error-text"
                role="alert"
                style={{ color: 'var(--md-sys-color-error)' }}
              >
                {saveError}
              </div>
            )}
          </SettingsCard>

          <SettingsCard icon="route" title={WORKSPACE_LABELS.pipeline}>
            <div className="field">
              <label htmlFor="pipeline-select">流水线</label>
              <select
                id="pipeline-select"
                value={settings.pipelineKey}
                onChange={(e) => setSettings({ pipelineKey: e.target.value })}
              >
                <option value="">请选择</option>
                {PIPELINE_OPTIONS.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'flex-end',
                gap: 8,
              }}
            >
              <md-filled-button
                onClick={() =>
                  saveSection('pipeline', {
                    pipelineKey: settings.pipelineKey,
                  })
                }
                disabled={isSaving || undefined}
              >
                保存
              </md-filled-button>
              {saveError && (
                <div
                  className="error-text"
                  role="alert"
                  style={{ color: 'var(--md-sys-color-error)' }}
                >
                  {saveError}
                </div>
              )}
            </div>
          </SettingsCard>

          <SettingsCard icon="smart_toy" title={WORKSPACE_LABELS.agents}>
            <AgentAllocationList workspaceId={workspaceId} />
          </SettingsCard>
        </div>
      </main>
    </div>
  )
}
