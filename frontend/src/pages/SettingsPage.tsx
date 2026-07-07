import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { IconButton } from '@mui/material'
import { useSettingStore } from '../stores/settingStore'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { ExecutorAllocationSection } from '../components/ExecutorAllocationSection'
import { ExecutorBindingSection } from '../components/ExecutorBindingSection'
import { LocalNodeLimitSection } from '../components/LocalNodeLimitSection'
import { MaterialIcon } from '../components/MaterialIcon'
import { BasicInfoSection } from '../components/settings/BasicInfoSection'
import { DangerZone } from '../components/settings/DangerZone'
import { WorkflowSection } from '../components/settings/WorkflowSection'
import { IntakeConfigSection } from '../components/settings/IntakeConfigSection'
import styles from './SettingsPage.module.css'

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

  useEffect(() => {
    if (!workspaceId) return
    setWorkspaceId(workspaceId)
    resetTestStatus()
    void fetchSettings(workspaceId)
  }, [workspaceId, setWorkspaceId, resetTestStatus, fetchSettings])

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
          <BasicInfoSection
            workspaceName={workspaceName}
            workspaceDescription={workspaceDescription}
            onNameChange={setWorkspaceName}
            onDescriptionChange={setWorkspaceDescription}
          />

          <IntakeConfigSection
            settings={settings}
            workflowDefinition={workflowDefinition}
            resourceProviders={resourceProviders}
            testStatus={testStatus}
            saveError={saveError}
            isTesting={isTesting}
            isSaving={isSaving}
            setSettings={setSettings}
            onTestConnection={testConnection}
          />

          <WorkflowSection
            workflowKey={settings.workflowKey}
            onChange={(key) => setSettings({ workflowKey: key })}
          />

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

          <DangerZone workspaceId={workspaceId} workspaceName={workspaceName} />
        </div>
      </div>
    </AppShell>
  )
}
