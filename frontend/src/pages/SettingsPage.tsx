import { useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { IconButton } from '@mui/material'
import { useSettingStore } from '../stores/settingStore'
import { useAuthStore } from '../stores/authStore'
import { useSettingsScrollSpy } from '../hooks/useSettingsScrollSpy'
import { useSettingStoreHydration } from '../hooks/useWorkspaceSettingsQuery'
import { useWorkflowDefinitionQuery } from '../hooks/useWorkflowDefinitionQuery'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { AgentRoutingSection } from '../components/AgentRoutingSection'
import { LocalNodeLimitSection } from '../components/LocalNodeLimitSection'
import { MaterialIcon } from '../components/MaterialIcon'
import { BasicInfoSection } from '../components/settings/BasicInfoSection'
import { DangerZone } from '../components/settings/DangerZone'
import { PreviewConfigSection } from '../components/settings/PreviewConfigSection'
import { WorkerTokensSection } from '../components/settings/WorkerTokensSection'
import { ApiAccessSection } from '../components/settings/ApiAccessSection'
import { WorkspaceWorkersSection } from '../components/settings/WorkspaceWorkersSection'
import { WorkerConsoleGuide } from '../components/settings/WorkerConsoleGuide'
import { WorkspaceMembersSection } from '../components/settings/WorkspaceMembersSection'
import { codePoolNodes } from '../components/codePoolNodes'
import { WorkspaceAgentsSection } from '../components/settings/WorkspaceAgentsSection'
import styles from './SettingsPage.module.css'

export function SettingsPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const {
    workspaceName,
    workspaceDescription,
    settings,
    setWorkspaceName,
    setWorkspaceDescription,
    setSettings,
    isDirty,
    isSaving,
    saveError,
    saveAll,
  } = useSettingStore()

  // 服务端快照经 react-query 拉取并水合进 store（切换工作区重置草稿；
  // 非 dirty 时同步后台 refetch）。loadError 由 hook 写入 saveError。
  const settingsQuery = useSettingStoreHydration(workspaceId)
  const settingsSnapshot = settingsQuery.data
  const { data: workflowDefinitionData } =
    useWorkflowDefinitionQuery(workspaceId)
  const workflowDefinition = workflowDefinitionData ?? null

  // P-0.5：节点级并发上限只对 code 池节点有意义（口径见 codePoolNodes，
  // #1079 起按 node_type 排除自含 agent 节点）。
  const codeNodeKeys = useMemo(() => {
    if (!workflowDefinition) return new Set<string>()
    return new Set(
      codePoolNodes(
        workflowDefinition.nodes,
        settingsSnapshot?.agentRoutes ?? []
      ).map((node) => node.key)
    )
  }, [workflowDefinition, settingsSnapshot])

  const hasCodeNodes = codeNodeKeys.size > 0

  const navItems = useMemo(
    () => [
      { id: 'basic-info', label: '基础信息' },
      { id: 'agent-workers', label: 'Agent 与 Worker' },
      // Agent 定义端点 admin-only（studio_secured），非 admin 不给入口（#677）；
      // #1079（#440 D1）起为只读历史。
      ...(isAdmin
        ? [{ id: 'workspace-agents', label: '历史 Agent 定义' }]
        : []),
      ...(isAdmin ? [{ id: 'api-access', label: '外部对接' }] : []),
      ...(isAdmin ? [{ id: 'workspace-members', label: '成员管理' }] : []),
      ...(hasCodeNodes
        ? [{ id: 'code-node-concurrency', label: '代码节点并发' }]
        : []),
      { id: 'danger-zone', label: '危险操作' },
    ],
    [hasCodeNodes, isAdmin]
  )

  const { activeSection, contentRef, scrollToSection } =
    useSettingsScrollSpy(navItems)

  if (!workspaceId) return null

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
            entityType={settings.entityType}
            saveError={saveError}
            onNameChange={setWorkspaceName}
            onDescriptionChange={setWorkspaceDescription}
            onEntityTypeChange={(entityType) => setSettings({ entityType })}
          />

          <PreviewConfigSection
            settings={settings}
            workflowDefinition={workflowDefinition}
            setSettings={setSettings}
          />

          {/* schema v62：workflow key 与 workspace id 绑定且不可变，
              原 WorkflowSection 编辑器已移除（后端 PATCH /configuration
              对 key 变更一律 400）。settings.workflowKey 已随
              #211 M3 从契约退役。 */}

          <section id="agent-workers" className={styles.section}>
            <h2 className={styles.sectionTitle}>Agent 与 Worker</h2>
            <hr className={styles.sectionDivider} />
            <AgentRoutingSection />
            <WorkerConsoleGuide isAdmin={isAdmin} />
            {isAdmin ? (
              <WorkerTokensSection workspaceId={workspaceId ?? ''} />
            ) : (
              <WorkspaceWorkersSection workspaceId={workspaceId ?? ''} />
            )}
          </section>
          {isAdmin && <WorkspaceAgentsSection workspaceId={workspaceId} />}
          {/* #870：API Token 与接入信息独立成「外部对接」一级 section——
              外部系统接入与 agent/worker 管理是两件事。Token 签发/吊销
              是 admin 动作（后端 require_admin）。 */}
          {isAdmin && (
            <section id="api-access" className={styles.section}>
              <h2 className={styles.sectionTitle}>外部对接</h2>
              <hr className={styles.sectionDivider} />
              <ApiAccessSection workspaceId={workspaceId} />
            </section>
          )}
          {isAdmin && <WorkspaceMembersSection workspaceId={workspaceId} />}
          {hasCodeNodes && (
            <section id="code-node-concurrency" className={styles.section}>
              <h2 className={styles.sectionTitle}>代码节点并发</h2>
              <hr className={styles.sectionDivider} />
              <LocalNodeLimitSection />
            </section>
          )}

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
