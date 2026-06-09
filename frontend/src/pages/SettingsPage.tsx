import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useSettingStore } from '../stores/settingStore'
import { SettingsCard } from '../components/SettingsCard'

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

  const isTesting = testStatus.state === 'testing'

  const toggleIntakeMode = (key: string) => {
    const next = settings.intakeModes.includes(key)
      ? settings.intakeModes.filter((k) => k !== key)
      : [...settings.intakeModes, key]
    setSettings({ intakeModes: next })
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
    <div style={{ maxWidth: 800, margin: '0 auto', padding: '8px 0 32px' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          marginBottom: 24,
        }}
      >
        <md-outlined-button
          onClick={() => navigate(`/workspaces/${workspaceId}`)}
        >
          ◀ 返回
        </md-outlined-button>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 500 }}>
          工作空间设置
        </h1>
      </div>

      <SettingsCard
        icon="📡"
        title="资源连接"
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

      <SettingsCard icon="📥" title="接入模式">
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

      <SettingsCard icon="🔄" title="流水线">
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

      <SettingsCard icon="🤖" title="智能体">
        <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
          智能体配置将在后续步骤实现
        </p>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <md-filled-button disabled>保存</md-filled-button>
        </div>
      </SettingsCard>
    </div>
  )
}
