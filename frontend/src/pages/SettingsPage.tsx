import { useEffect, useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { IconButton } from '@mui/material'
import { useSettingStore } from '../stores/settingStore'
import { useAuthStore } from '../stores/authStore'
import { useSettingsScrollSpy } from '../hooks/useSettingsScrollSpy'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { ExecutorAllocationSection } from '../components/ExecutorAllocationSection'
import { AgentRoutingSection } from '../components/AgentRoutingSection'
import { ExecutorBindingSection } from '../components/ExecutorBindingSection'
import { LocalNodeLimitSection } from '../components/LocalNodeLimitSection'
import { MaterialIcon } from '../components/MaterialIcon'
import { BasicInfoSection } from '../components/settings/BasicInfoSection'
import { DangerZone } from '../components/settings/DangerZone'
import { WorkflowSection } from '../components/settings/WorkflowSection'
import { IntakeConfigSection } from '../components/settings/IntakeConfigSection'
import { NodeConfigSection } from '../components/settings/NodeConfigSection'
import { WorkerTokensSection } from '../components/settings/WorkerTokensSection'
import { WorkspaceMembersSection } from '../components/settings/WorkspaceMembersSection'
import styles from './SettingsPage.module.css'

export function SettingsPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
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

  const hasNodeConfig = Object.keys(settings.nodeConfigSchemas ?? {}).length > 0

  const navItems = useMemo(
    () => [
      { id: 'basic-info', label: '基础信息' },
      { id: 'intake-config', label: '接入与资源' },
      ...(hasNodeConfig ? [{ id: 'node-config', label: '节点配置' }] : []),
      { id: 'workflow', label: '工作流' },
      { id: 'executors', label: '执行器' },
      { id: 'agent-workers', label: 'Agent 与 Worker' },
      ...(isAdmin ? [{ id: 'workspace-members', label: '成员管理' }] : []),
      { id: 'danger-zone', label: '危险操作' },
    ],
    [hasNodeConfig, isAdmin]
  )

  const { activeSection, contentRef, scrollToSection } =
    useSettingsScrollSpy(navItems)

  useEffect(() => {
    if (!workspaceId) return
    setWorkspaceId(workspaceId)
    resetTestStatus()
    void fetchSettings(workspaceId)
  }, [workspaceId, setWorkspaceId, resetTestStatus, fetchSettings])

  useEffect(() => {
    if (!workspaceId) return
    void fetchResourceProviders()
  }, [workspaceId, fetchResourceProviders])

  useEffect(() => {
    if (!settings.workflowKey) return
    void fetchWorkflowDefinition()
  }, [settings.workflowKey, fetchWorkflowDefinition])

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
              <li key={item.id}>
                <button
                  type="button"
                  className={
                    activeSection === item.id
                      ? styles.navItemActive
                      : styles.navItem
                  }
                  aria-current={activeSection === item.id ? 'true' : undefined}
                  onClick={() => scrollToSection(item.id)}
                >
                  {item.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className={styles.contentArea} ref={contentRef}>
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

          <NodeConfigSection
            workspaceId={workspaceId}
            settings={settings}
            workflowDefinition={workflowDefinition}
          />

          <WorkflowSection
            workflowKey={settings.workflowKey}
            onChange={(key) => setSettings({ workflowKey: key })}
          />

          <section id="executors" className={styles.section}>
            <h2 className={styles.sectionTitle}>执行器</h2>
            <hr className={styles.sectionDivider} />
            <ExecutorAllocationSection />
            <ExecutorBindingSection />
            {hasLocalNodes && <LocalNodeLimitSection />}
          </section>
          <section id="agent-workers" className={styles.section}>
            <h2 className={styles.sectionTitle}>Agent 与 Worker</h2>
            <hr className={styles.sectionDivider} />
            <AgentRoutingSection />
            <WorkerTokensSection />
          </section>
          {isAdmin && <WorkspaceMembersSection workspaceId={workspaceId} />}

          <section id="danger-zone" className={styles.section}>
            <h2 className={styles.sectionTitle}>危险操作</h2>
            <hr className={styles.sectionDivider} />
            <DangerZone
              workspaceId={workspaceId}
              workspaceName={workspaceName}
            />
          </section>
        </div>
      </div>
    </AppShell>
  )
}
