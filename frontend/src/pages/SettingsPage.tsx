import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  IconButton,
  TextField,
  Button,
  Checkbox,
  MenuItem,
} from '@mui/material'
import { useSettingStore } from '../stores/settingStore'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { ExecutorAllocationSection } from '../components/ExecutorAllocationSection'
import { ExecutorBindingSection } from '../components/ExecutorBindingSection'
import { LocalNodeLimitSection } from '../components/LocalNodeLimitSection'
import { MaterialIcon } from '../components/MaterialIcon'
import { fetchWorkflows } from '../api'
import styles from './SettingsPage.module.css'

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
  const {
    setWorkspaceId,
    workspaceName,
    workspaceDescription,
    settings,
    setWorkspaceName,
    setWorkspaceDescription,
    setSettings,
    isDirty,
    isSaving,
    saveError,
    resourceProviders,
    workflowDefinition,
    executorCatalog,
    executorConfiguration,
    testStatus,
    saveAll,
    testConnection,
    resetTestStatus,
    fetchSettings,
    fetchGlobalServices,
    fetchResourceProviders,
    fetchWorkflowDefinition,
  } = useSettingStore()

  const localBoundNodeKeys = useMemo(() => {
    if (!workflowDefinition) return new Set<string>()
    const allocatedIds = new Set(
      executorConfiguration.allocations.map((a) => a.executor_id)
    )
    return new Set(
      workflowDefinition.nodes
        .filter((node) => {
          const binding = executorConfiguration.bindings.find(
            (b) =>
              b.workflow_key === workflowDefinition.key &&
              b.node_key === node.key
          )
          if (!binding || !allocatedIds.has(binding.executor_id)) return false
          const executor = executorCatalog.find(
            (e) => e.id === binding.executor_id
          )
          return executor?.kind === 'local'
        })
        .map((node) => node.key)
    )
  }, [workflowDefinition, executorConfiguration, executorCatalog])

  const hasLocalNodes = localBoundNodeKeys.size > 0

  const navItems = useMemo(
    () => [
      { id: 'basic-info', label: '基础信息' },
      { id: 'intake-config', label: '接入与资源' },
      { id: 'workflow', label: '工作流' },
      { id: 'executor-allocation', label: '执行器分配' },
      { id: 'executor-binding', label: '节点绑定' },
      ...(hasLocalNodes
        ? [{ id: 'local-node-concurrency', label: '本地节点并发' }]
        : []),
    ],
    [hasLocalNodes]
  )

  const [activeSection, setActiveSection] = useState('basic-info')
  const [workflowOptions, setWorkflowOptions] = useState<
    Array<{ key: string; label: string }>
  >([])

  useEffect(() => {
    if (!workspaceId) return
    setWorkspaceId(workspaceId)
    resetTestStatus()
    void fetchSettings(workspaceId)
  }, [
    workspaceId,
    setWorkspaceId,
    resetTestStatus,
    fetchSettings,
    fetchWorkflowDefinition,
  ])

  useEffect(() => {
    fetchWorkflows()
      .then((data) => {
        setWorkflowOptions(
          data.workflows.map((p) => ({ key: p.key, label: p.label }))
        )
      })
      .catch(() => {
        setWorkflowOptions([])
      })
  }, [])

  useEffect(() => {
    if (!workspaceId) return
    void fetchGlobalServices()
    void fetchResourceProviders()
  }, [workspaceId, fetchGlobalServices, fetchResourceProviders])

  useEffect(() => {
    if (!settings.workflowKey) return
    void fetchWorkflowDefinition()
  }, [settings.workflowKey, fetchWorkflowDefinition])

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

    const mode = workflowDefinition?.intake?.modes.find((m) => m.key === key)
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
      <IconButton
        onClick={() => void saveAll()}
        disabled={!isDirty || isSaving}
        aria-label="保存"
      >
        <MaterialIcon name="save" />
      </IconButton>
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
              <TextField
                label="Workspace 名称"
                variant="outlined"
                value={workspaceName}
                onChange={(event) => setWorkspaceName(event.target.value)}
                fullWidth
              />
            </div>
            <div className={styles.field}>
              <TextField
                label="描述"
                variant="outlined"
                multiline
                rows={2}
                value={workspaceDescription}
                onChange={(event) =>
                  setWorkspaceDescription(event.target.value)
                }
                fullWidth
              />
            </div>
          </section>

          <section id="intake-config" className={styles.section}>
            <h2 className={styles.sectionTitle}>接入与资源</h2>
            <hr className={styles.sectionDivider} />
            <div className={styles.field}>
              <TextField
                select
                label="默认实体类型"
                variant="outlined"
                value={settings.entityType}
                onChange={(e) =>
                  setSettings({
                    entityType: e.target.value as
                      | 'question'
                      | 'knowledge'
                      | 'video',
                  })
                }
                fullWidth
              >
                <MenuItem value="question">question</MenuItem>
                <MenuItem value="knowledge">knowledge</MenuItem>
                <MenuItem value="video">video</MenuItem>
              </TextField>
            </div>

            <div className={styles.field}>
              <span
                style={{
                  fontSize: 12,
                  color: '#616161',
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
                {(workflowDefinition?.intake?.modes || []).map((mode) => {
                  const isChecked = settings.intakeModes.includes(mode.key)
                  return (
                    <div
                      key={mode.key}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                      }}
                    >
                      <Checkbox
                        checked={isChecked}
                        onChange={() => toggleIntakeMode(mode.key)}
                      />
                      <span style={{ fontSize: 14 }}>{mode.label}</span>
                    </div>
                  )
                })}
              </div>
            </div>

            {(() => {
              const activeKeys = new Set<string>()
              for (const mode of workflowDefinition?.intake?.modes || []) {
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
                      color: '#616161',
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
                            border: '1px solid #e0e0e0',
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
                              color: '#616161',
                              marginBottom: 12,
                            }}
                          >
                            Path: {provider.path}
                          </div>
                          <div style={{ display: 'grid', gap: 8 }}>
                            {provider.paramKeys.map((paramKey) => (
                              <TextField
                                key={paramKey}
                                label={paramKey}
                                variant="outlined"
                                placeholder={
                                  provider.defaultParams[paramKey] || ''
                                }
                                value={binding.config[paramKey] || ''}
                                onChange={(event) => {
                                  const value = event.target.value
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
                                fullWidth
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
              <Button
                variant="outlined"
                onClick={testConnection}
                disabled={isTesting || isSaving}
              >
                {isTesting ? '测试中...' : '测试连接'}
              </Button>
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
                style={{ color: '#d32f2f', marginTop: 12 }}
              >
                {saveError}
              </div>
            )}
          </section>

          <section id="workflow" className={styles.section}>
            <h2 className={styles.sectionTitle}>工作流</h2>
            <hr className={styles.sectionDivider} />
            <div className={styles.field}>
              <TextField
                select
                label="工作流"
                variant="outlined"
                value={settings.workflowKey || ''}
                onChange={(e) =>
                  setSettings({
                    workflowKey: e.target.value,
                  })
                }
                fullWidth
              >
                <MenuItem value="">请选择</MenuItem>
                {workflowOptions.map((p) => (
                  <MenuItem key={p.key} value={p.key}>
                    {p.label}
                  </MenuItem>
                ))}
              </TextField>
            </div>
          </section>

          <section id="executor-allocation" className={styles.section}>
            <h2 className={styles.sectionTitle}>执行器分配</h2>
            <hr className={styles.sectionDivider} />
            <ExecutorAllocationSection />
          </section>

          <section id="executor-binding" className={styles.section}>
            <h2 className={styles.sectionTitle}>节点绑定</h2>
            <hr className={styles.sectionDivider} />
            <ExecutorBindingSection />
          </section>

          {hasLocalNodes && (
            <section id="local-node-concurrency" className={styles.section}>
              <h2 className={styles.sectionTitle}>本地节点并发</h2>
              <hr className={styles.sectionDivider} />
              <LocalNodeLimitSection />
            </section>
          )}
        </div>
      </div>
    </AppShell>
  )
}
