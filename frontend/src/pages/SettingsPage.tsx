import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useSettingStore } from '../stores/settingStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { AgentAllocationList } from '../components/AgentAllocationList'
import { SettingsCard } from '../components/SettingsCard'
import { WORKSPACE_LABELS } from '../labels'

const INTAKE_MODE_OPTIONS = [
  { key: 'direct_ids', label: '直接输入 ID' },
  { key: 'by_knowledge', label: '按知识点' },
  { key: 'batch_upload', label: '批量上传' },
]

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

export function SettingsPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const {
    setWorkspaceId,
    settings,
    setSettings,
    testStatus,
    isSaving,
    saveError,
    testConnection,
    resetTestStatus,
    saveSection,
    fetchSettings,
  } = useSettingStore()

  const [labelOverridesText, setLabelOverridesText] = useState('')

  const { workspaces, fetchWorkspaces, updateWorkspace } = useWorkspaceStore()

  const [workspaceEditName, setWorkspaceEditName] = useState('')
  const [workspaceEditDescription, setWorkspaceEditDescription] = useState('')
  const [isSavingWorkspace, setIsSavingWorkspace] = useState(false)
  const [workspaceSaveError, setWorkspaceSaveError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspaceId) return
    setWorkspaceId(workspaceId)
    resetTestStatus()
    void fetchSettings(workspaceId).then(() => {
      setLabelOverridesText(
        JSON.stringify(
          useSettingStore.getState().settings.labelOverrides,
          null,
          2
        )
      )
    })
  }, [workspaceId, setWorkspaceId, resetTestStatus, fetchSettings])

  useEffect(() => {
    if (workspaces.length === 0) {
      fetchWorkspaces()
    }
  }, [workspaces.length, fetchWorkspaces])

  const workspace = workspaceId
    ? workspaces.find((w) => w.id === workspaceId)
    : undefined
  const workspaceName = workspace?.name || workspaceId || ''

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => {
    if (workspace) {
      setWorkspaceEditName(workspace.name)
      setWorkspaceEditDescription(workspace.description || '')
    }
  }, [workspace])

  const isTesting = testStatus.state === 'testing'

  const toggleIntakeMode = (key: string) => {
    const next = settings.intakeModes.includes(key)
      ? settings.intakeModes.filter((k) => k !== key)
      : [...settings.intakeModes, key]
    setSettings({ intakeModes: next })
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

  const handleLabelOverridesInput = (event: Event) => {
    const value = (event.target as HTMLInputElement).value
    setLabelOverridesText(value)
    try {
      const parsed = value.trim() === '' ? {} : JSON.parse(value)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        setSettings({ labelOverrides: parsed as Record<string, string> })
      }
    } catch {
      // Ignore parse errors while the user is typing.
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
                setWorkspaceEditDescription((event.target as HTMLInputElement).value)
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

          <SettingsCard
        icon="settings_remote"
        title={WORKSPACE_LABELS.resources}
        status={
          <div aria-live="polite" aria-atomic="true">
            {connectionStatus}
          </div>
        }
        defaultExpanded
      >
        <md-outlined-text-field
          label="CMS 地址"
          type="url"
          value={settings.cmsUrl}
          onInput={(event: Event) =>
            setSettings({
              cmsUrl: (event.target as HTMLInputElement).value,
            })
          }
          style={{ width: '100%' }}
        />
        <md-outlined-text-field
          label="CMS Token"
          type="password"
          value={settings.cmsToken}
          onInput={(event: Event) =>
            setSettings({
              cmsToken: (event.target as HTMLInputElement).value,
            })
          }
          style={{ width: '100%' }}
        />
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <md-outlined-button
            onClick={testConnection}
            disabled={isTesting || isSaving || undefined}
          >
            {isTesting ? '测试中...' : '测试连接'}
          </md-outlined-button>
          <md-filled-button
            onClick={() =>
              saveSection('connection', {
                cmsUrl: settings.cmsUrl,
                cmsToken: settings.cmsToken,
              })
            }
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

      <SettingsCard icon="input" title={WORKSPACE_LABELS.intake}>
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
            启用的接入模式
          </span>
          <div className="intake-chip-row">
            {INTAKE_MODE_OPTIONS.map((mode) => (
              <md-filter-chip
                key={mode.key}
                label={mode.label}
                selected={settings.intakeModes.includes(mode.key)}
                onClick={() => toggleIntakeMode(mode.key)}
              />
            ))}
          </div>
        </div>

        <md-outlined-text-field
          label="标签覆盖 (JSON)"
          type="textarea"
          rows={3}
          value={labelOverridesText}
          onInput={handleLabelOverridesInput}
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
            onClick={() =>
              saveSection('intake', {
                entityType: settings.entityType,
                intakeModes: settings.intakeModes,
                labelOverrides: settings.labelOverrides,
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
              saveSection('pipeline', { pipelineKey: settings.pipelineKey })
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
