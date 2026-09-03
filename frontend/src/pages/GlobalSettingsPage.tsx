import { useEffect, useMemo } from 'react'
import { useLocation } from 'react-router-dom'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { useAuthStore } from '../stores/authStore'
import { useSettingsScrollSpy } from '../hooks/useSettingsScrollSpy'
import { useQuery } from '@tanstack/react-query'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { toErrorMessage } from '../lib/queryError'
import { getTokenUsagePricing } from '../api/tokenUsagePricing'
import { InfraConnectionsSection } from './globalSettings/InfraConnectionsSection'
import { InstanceSettingsSection } from './globalSettings/InstanceSettingsSection'
import { ConnectionsSection } from './globalSettings/ConnectionsSection'
import { StudioAgentsSection } from './globalSettings/StudioAgentsSection'
import { ModelPricingCard } from './globalSettings/ModelPricingCard'
import styles from './GlobalSettingsPage.module.css'

// 侧栏只留本页五个区块的锚点导航：分组标题、onboarding 回补入口与
// workspace 指引均已退役（#333 的两层心智降为一层——onboarding 走
// bootstrap 跳转，workspace 设置入口在各 workspace 自己的 UI 内）。
const navItems = [
  { id: 'studio-agents', label: 'Studio Agent 管理' },
  { id: 'connections', label: '外部服务连接' },
  { id: 'infra-connections', label: '基础设施连接' },
  { id: 'instance-settings', label: '实例设置' },
  { id: 'model-pricing', label: '模型定价' },
]

/** 挂载时按 URL hash（#studio-agents 等）滚动到对应区块。 */
function useSectionAnchor() {
  const { hash } = useLocation()
  useEffect(() => {
    if (!hash) return
    const id = hash.slice(1)
    if (!navItems.some((item) => item.id === id)) return
    const element = document.getElementById(id)
    if (element) {
      element.scrollIntoView()
    }
  }, [hash])
}

export default function GlobalSettingsPage() {
  const currentUser = useAuthStore((s) => s.user)
  const isAdmin = currentUser?.role === 'admin'

  const { data, error: loadQueryError } = useQuery({
    queryKey: extraQueryKeys.tokenUsagePricing(),
    queryFn: getTokenUsagePricing,
    enabled: isAdmin,
  })
  const loadError = toErrorMessage(loadQueryError)

  const { activeSection, contentRef, scrollToSection } = useSettingsScrollSpy(
    useMemo(() => navItems, []),
    'studio-agents'
  )
  useSectionAnchor()

  if (!isAdmin) {
    return (
      <AppShell
        appBar={({ scrolled }) => (
          <AppBar title="全局设置" backTo="/" scrolled={scrolled} />
        )}
      >
        <div className={styles.main}>
          <p className={styles.empty}>无权限访问，仅管理员可管理全局设置。</p>
        </div>
      </AppShell>
    )
  }

  if (loadError) {
    return (
      <AppShell
        appBar={({ scrolled }) => (
          <AppBar title="全局设置" backTo="/" scrolled={scrolled} />
        )}
      >
        <div className={styles.main}>
          <p className={styles.error} role="alert">
            {loadError}
          </p>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar title="全局设置" backTo="/" scrolled={scrolled} />
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
          <section id="studio-agents">
            <StudioAgentsSection />
          </section>
          <section id="connections">
            <ConnectionsSection />
          </section>
          <section id="infra-connections">
            <InfraConnectionsSection />
          </section>
          <section id="instance-settings">
            <InstanceSettingsSection />
          </section>
          <section id="model-pricing">
            {/* 每个卡片自带独立保存；定价数据未就绪时其余区块照常渲染。 */}
            {data ? <ModelPricingCard initial={data} /> : null}
          </section>
        </div>
      </div>
    </AppShell>
  )
}
