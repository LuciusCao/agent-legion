import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useSettingStore } from '../stores/settingStore'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { AgentAllocationList } from '../components/AgentAllocationList'
import { VIDEO_HIVE_ID } from '../layouts/WorkspaceLayout'
import styles from './SettingsPage.module.css'

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
  const isVideoHive = workspaceId === VIDEO_HIVE_ID
  const {
    setWorkspaceId,
    workspaceName,
    workspaceDescription,
    settings,
    setWorkspaceName,
    setWorkspaceDescription,
    setSettings,
    agentAssignments,
    setAgentAssignments,
    isDirty,
    isSaving,
    saveError,
    resourceProviders,
    pipelineDefinition,
    testStatus,
    saveAll,
    testConnection,
    resetTestStatus,
    fetchSettings,
    fetchAgentAssignments,
    fetchGlobalServices,
    fetchResourceProviders,
    fetchPipelineDefinition,
  } = useSettingStore()

  const navItems = useMemo(
    () => [
      { id: 'basic-info', label: '基本信息' },
      { id: 'intake-config', label: '接入配置' },
      { id: 'pipeline', label: 'Pipeline' },
      ...(isVideoHive ? [{ id: 'agents', label: '智能体' }] : []),
    ],
    [isVideoHive]
  )

  const [activeSection, setActiveSection] = useState('basic-info')

  useEffect(() => {
    if (!workspaceId) return
    setWorkspaceId(workspaceId)
    resetTestStatus()
    void fetchSettings(workspaceId).then(() => {
      void fetchPipelineDefinition()
    })
    if (isVideoHive) {
      void fetchAgentAssignments(workspaceId)
    }
  }, [
    workspaceId,
    isVideoHive,
    setWorkspaceId,
    resetTestStatus,
    fetchSettings,
    fetchPipelineDefinition,
    fetchAgentAssignments,
  ])

  useEffect(() => {
    if (!workspaceId) return
    void fetchGlobalServices()
    void fetchResourceProviders()
  }, [workspaceId, fetchGlobalServices, fetchResourceProviders])

  const scrollToSection = useCallback((id: string) => {
    setActiveSection(id)
    const el = document.getElementById(id)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth' })
    }
  }, [])

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

  const isTesting = testStatus.state === 'testing'

  const rightActions = (
    <div className={styles.saveButtonWrap}>
      <md-icon-button
        onClick={() => void saveAll()}
        disabled={!isDirty || isSaving || undefined}
        aria-label="保存"
      >
        <md-icon>save</md-icon>
      </md-icon-button>
      {isDirty && <span className={styles.saveBadge} aria-hidden="true" />}
    </div>
  )

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title={`${workspaceName} / 设置`}
          backTo={`/workspaces/${workspaceId}`}
          scrolled={scrolled}
          rightActions={rightActions}
        />
      )}
      mainClassName="settings-main"
    >
      <div className={styles.settingsLayout}>
        <nav className={styles.navSidebar}>
          <ul className={styles.navList}>
            {navItems.map((item) => (
              <li
                key={item.id}
                className={
                  activeSection === item.id
                    ? styles.navItemActive
                    : styles.navItem
                }
                onClick={() => scrollToSection(item.id)}
              >
                {item.label}
              </li>
            ))}
          </ul>
        </nav>

        <div className={styles.contentArea}>
          <section id="basic-info" className={styles.section}>
            <h2 className={styles.sectionTitle}>基本信息</h2>
            <hr className={styles.sectionDivider} />
            <div className={styles.field}>
              <md-outlined-text-field
                label="Workspace 名称"
                value={workspaceName}
                onInput={(event: Event) =>
                  setWorkspaceName((event.target as HTMLInputElement).value)
                }
                style={{ width: '100%' }}
              />
            </div>
            <div className={styles.field}>
              <md-outlined-text-field
                label="描述"
                type="textarea"
                rows={2}
                value={workspaceDescription}
                onInput={(event: Event) =>
                  setWorkspaceDescription(
                    (event.target as HTMLInputElement).value
                  )
                }
                style={{ width: '100%' }}
              />
            </div>
          </section>

          <section id="intake-config" className={styles.section}>
            <h2 className={styles.sectionTitle}>接入配置</h2>
            <hr className={styles.sectionDivider} />
            <div className={styles.field}>
              <md-outlined-select
                label="默认实体类型"
                value={settings.entityType}
                onChange={(e: React.FormEvent<HTMLSelectElement>) =>
                  setSettings({
                    entityType: (e.target as HTMLSelectElement).value as
                      | 'question'
                      | 'knowledge'
                      | 'video',
                  })
                }
              >
                <md-select-option value="question">
                  <div slot="headline">question</div>
                </md-select-option>
                <md-select-option value="knowledge">
                  <div slot="headline">knowledge</div>
                </md-select-option>
                <md-select-option value="video">
                  <div slot="headline">video</div>
                </md-select-option>
              </md-outlined-select>
            </div>

            <div className={styles.field}>
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
                  <span
                    style={{
                      fontSize: 12,
                      color: 'var(--md-sys-color-on-surface-variant)',
                    }}
                  >
                    资源接口参数
                  </span>
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
              <div aria-live="polite" aria-atomic="true">
                <ConnectionStatusPill
                  state={testStatus.state}
                  message={testStatus.message}
                />
              </div>
            </div>
            {saveError && (
              <div
                className="error-text"
                role="alert"
                style={{ color: 'var(--md-sys-color-error)', marginTop: 12 }}
              >
                {saveError}
              </div>
            )}
          </section>

          <section id="pipeline" className={styles.section}>
            <h2 className={styles.sectionTitle}>Pipeline</h2>
            <hr className={styles.sectionDivider} />
            <div className={styles.field}>
              <md-outlined-select
                label="流水线"
                value={settings.pipelineKey || ''}
                onChange={(e: React.FormEvent<HTMLSelectElement>) =>
                  setSettings({
                    pipelineKey: (e.target as HTMLSelectElement).value,
                  })
                }
              >
                <md-select-option value="">
                  <div slot="headline">请选择</div>
                </md-select-option>
                {PIPELINE_OPTIONS.map((p) => (
                  <md-select-option key={p.key} value={p.key}>
                    <div slot="headline">{p.label}</div>
                  </md-select-option>
                ))}
              </md-outlined-select>
            </div>
            <div className={styles.field}>
              <label htmlFor="local-concurrency">本地并发限制</label>
              <md-outlined-text-field
                id="local-concurrency"
                type="number"
                min={1}
                value={settings.localConcurrency ?? ''}
                onInput={(event: Event) => {
                  const value = Number((event.target as HTMLInputElement).value)
                  setSettings({
                    localConcurrency: Number.isNaN(value) ? undefined : value,
                  })
                }}
                style={{ width: '100%' }}
              />
            </div>
            <div className={styles.field}>
              <label htmlFor="agent-concurrency">智能体并发限制</label>
              <md-outlined-text-field
                id="agent-concurrency"
                type="number"
                min={1}
                value={settings.agentConcurrency ?? ''}
                onInput={(event: Event) => {
                  const value = Number((event.target as HTMLInputElement).value)
                  setSettings({
                    agentConcurrency: Number.isNaN(value) ? undefined : value,
                  })
                }}
                style={{ width: '100%' }}
              />
            </div>
          </section>

          {isVideoHive && (
            <section id="agents" className={styles.section}>
              <h2 className={styles.sectionTitle}>智能体</h2>
              <AgentAllocationList
                workspaceId={workspaceId}
                assignments={agentAssignments}
                onAssignmentsChange={setAgentAssignments}
              />
            </section>
          )}
        </div>
      </div>
    </AppShell>
  )
}
