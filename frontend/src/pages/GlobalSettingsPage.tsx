import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { IconButton } from '@mui/material'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { MaterialIcon } from '../components/MaterialIcon'
import { useAuthStore } from '../stores/authStore'
import { useUiStore } from '../stores/uiStore'
import { useSettingsScrollSpy } from '../hooks/useSettingsScrollSpy'
import { useQuery } from '@tanstack/react-query'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { toErrorMessage } from '../lib/queryError'
import {
  getTokenUsagePricing,
  updateTokenUsagePricing,
} from '../api/tokenUsagePricing'
import type {
  TokenUsagePricingConfigResponse,
  TokenUsagePricingRate,
} from '../api/tokenUsagePricing'
import { InstanceSettingsSection } from './globalSettings/InstanceSettingsSection'
import { ConnectionsSection } from './globalSettings/ConnectionsSection'
import { StudioAgentsSection } from './globalSettings/StudioAgentsSection'
import {
  EMPTY_ROW,
  ModelPricingSection,
} from './globalSettings/ModelPricingSection'
import type { RateRow } from './globalSettings/ModelPricingSection'
import { GLOBAL_ONBOARDING_PATH } from './GlobalOnboardingPage.storage'
import styles from './GlobalSettingsPage.module.css'

function toRows(pricing: TokenUsagePricingRate[]): RateRow[] {
  return pricing.map((rate) => ({
    provider: rate.provider,
    model: rate.model,
    input_per_1m: String(rate.input_per_1m),
    output_per_1m: String(rate.output_per_1m),
    cache_read_per_1m: String(rate.cache_read_per_1m),
  }))
}

function serialize(currency: string, rows: RateRow[]): string {
  return JSON.stringify({
    currency: currency.trim(),
    pricing: rows.map((row) => ({
      provider: row.provider.trim(),
      model: row.model.trim(),
      input_per_1m: Number(row.input_per_1m),
      output_per_1m: Number(row.output_per_1m),
      cache_read_per_1m: Number(row.cache_read_per_1m),
    })),
  })
}

function GlobalSettingsEditor({
  initial,
}: {
  initial: TokenUsagePricingConfigResponse
}) {
  const navigate = useNavigate()
  const [currency, setCurrency] = useState(initial.currency)
  const [rows, setRows] = useState<RateRow[]>(() => toRows(initial.pricing))
  const [baseline, setBaseline] = useState(() =>
    serialize(initial.currency, toRows(initial.pricing))
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const isDirty = serialize(currency, rows) !== baseline

  function buildPayload() {
    const pricing = []
    for (const row of rows) {
      const provider = row.provider.trim()
      const model = row.model.trim()
      if (!provider || !model) {
        throw new Error('每行的 provider 和 model 不能为空')
      }
      const rates = [
        row.input_per_1m,
        row.output_per_1m,
        row.cache_read_per_1m,
      ].map((value) => Number(value))
      if (rates.some((value) => !Number.isFinite(value) || value < 0)) {
        throw new Error('费率必须是不小于 0 的数字')
      }
      pricing.push({
        provider,
        model,
        input_per_1m: rates[0],
        output_per_1m: rates[1],
        cache_read_per_1m: rates[2],
      })
    }
    if (!currency.trim()) {
      throw new Error('货币单位不能为空')
    }
    return { currency: currency.trim(), pricing }
  }

  async function handleSave() {
    setError('')
    setSaving(true)
    try {
      const result = await updateTokenUsagePricing(buildPayload())
      setBaseline(serialize(result.currency, toRows(result.pricing)))
      useUiStore.getState().showToast('全局设置已保存', 'success')
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const rightActions = (
    <div className={styles.saveButtonWrap}>
      <IconButton
        onClick={() => void handleSave()}
        disabled={!isDirty || saving}
        aria-label="保存"
      >
        <MaterialIcon name="save" />
      </IconButton>
      {isDirty && <span className={styles.saveBadge} aria-hidden="true" />}
    </div>
  )

  // #333 两层心智：导航按「全局（实例级）」分组并对齐全局 onboarding
  // 清单（ACP agent 优先），workspace 级设置入口在侧栏底部说明。
  const navItems = useMemo(
    () => [
      { id: 'studio-agents', label: 'Studio Agent 管理' },
      { id: 'connections', label: '外部服务连接' },
      { id: 'instance-settings', label: '实例设置' },
      { id: 'model-pricing', label: '模型定价' },
    ],
    []
  )
  const { activeSection, contentRef, scrollToSection } = useSettingsScrollSpy(
    navItems,
    'studio-agents'
  )

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title="全局设置"
          backTo="/"
          scrolled={scrolled}
          rightActions={rightActions}
        />
      )}
      mainClassName="settings-main"
    >
      <div className={styles.settingsLayout}>
        <nav className={styles.navSidebar}>
          <p className={styles.navGroupTitle}>全局（实例级）</p>
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
          <p className={styles.navGroupTitle}>Workspace 级</p>
          <p className={styles.navHint}>各 workspace 的设置在其「设置」页。</p>
          <button
            type="button"
            className={styles.navItem}
            onClick={() => navigate(GLOBAL_ONBOARDING_PATH)}
          >
            全局初始化清单
          </button>
        </nav>

        <div className={styles.contentArea} ref={contentRef}>
          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
          <section id="studio-agents">
            <StudioAgentsSection />
          </section>
          <section id="connections">
            <ConnectionsSection />
          </section>
          <section id="instance-settings">
            <InstanceSettingsSection />
          </section>
          <section id="model-pricing">
            <ModelPricingSection
              currency={currency}
              rows={rows}
              onCurrencyChange={setCurrency}
              onRowChange={(index, patch) =>
                setRows((prev) =>
                  prev.map((row, i) =>
                    i === index ? { ...row, ...patch } : row
                  )
                )
              }
              onAddRow={() => setRows((prev) => [...prev, { ...EMPTY_ROW }])}
              onRemoveRow={(index) =>
                setRows((prev) => prev.filter((_, i) => i !== index))
              }
            />
          </section>
        </div>
      </div>
    </AppShell>
  )
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

  if (!data) return null

  return <GlobalSettingsEditor initial={data} />
}
