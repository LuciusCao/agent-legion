import { useState } from 'react'
import { IconButton } from '@mui/material'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { MaterialIcon } from '../components/MaterialIcon'
import { useAuthStore } from '../stores/authStore'
import { useUiStore } from '../stores/uiStore'
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
import styles from './GlobalSettingsPage.module.css'

interface RateRow {
  provider: string
  model: string
  input_per_1m: string
  output_per_1m: string
  cache_read_per_1m: string
}

const EMPTY_ROW: RateRow = {
  provider: '',
  model: '',
  input_per_1m: '',
  output_per_1m: '',
  cache_read_per_1m: '',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

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

interface ModelPricingSectionProps {
  currency: string
  rows: RateRow[]
  onCurrencyChange: (value: string) => void
  onRowChange: (index: number, patch: Partial<RateRow>) => void
  onAddRow: () => void
  onRemoveRow: (index: number) => void
}

function ModelPricingSection({
  currency,
  rows,
  onCurrencyChange,
  onRowChange,
  onAddRow,
  onRemoveRow,
}: ModelPricingSectionProps) {
  return (
    <div className={styles.card}>
      <h3 className={styles.heading}>模型定价</h3>
      <p className={styles.hint}>
        按 provider + model 配置每百万 token 价格；历史 run 按各自使用的
        provider + model 匹配价格分别计费，用于统计各 workspace 的成本消耗。
      </p>
      <div className={styles.row}>
        <label className={styles.label} htmlFor="pricing-currency">
          货币单位
        </label>
        <input
          id="pricing-currency"
          className={styles.currencyInput}
          value={currency}
          onChange={(e) => onCurrencyChange(e.target.value)}
        />
      </div>

      <table className={styles.table}>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Model</th>
            <th>输入 / 1M</th>
            <th>输出 / 1M</th>
            <th>缓存读 / 1M</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} data-testid={`pricing-row-${index}`}>
              <td>
                <input
                  className={styles.input}
                  aria-label={`provider-${index}`}
                  value={row.provider}
                  onChange={(e) =>
                    onRowChange(index, { provider: e.target.value })
                  }
                />
              </td>
              <td>
                <input
                  className={styles.input}
                  aria-label={`model-${index}`}
                  value={row.model}
                  onChange={(e) =>
                    onRowChange(index, { model: e.target.value })
                  }
                />
              </td>
              <td>
                <input
                  className={styles.input}
                  aria-label={`input-rate-${index}`}
                  type="number"
                  min="0"
                  step="any"
                  value={row.input_per_1m}
                  onChange={(e) =>
                    onRowChange(index, { input_per_1m: e.target.value })
                  }
                />
              </td>
              <td>
                <input
                  className={styles.input}
                  aria-label={`output-rate-${index}`}
                  type="number"
                  min="0"
                  step="any"
                  value={row.output_per_1m}
                  onChange={(e) =>
                    onRowChange(index, { output_per_1m: e.target.value })
                  }
                />
              </td>
              <td>
                <input
                  className={styles.input}
                  aria-label={`cache-rate-${index}`}
                  type="number"
                  min="0"
                  step="any"
                  value={row.cache_read_per_1m}
                  onChange={(e) =>
                    onRowChange(index, { cache_read_per_1m: e.target.value })
                  }
                />
              </td>
              <td>
                <button
                  type="button"
                  className={styles.dangerButton}
                  onClick={() => onRemoveRow(index)}
                >
                  删除
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <button type="button" className={styles.textButton} onClick={onAddRow}>
        添加一行
      </button>
    </div>
  )
}

function GlobalSettingsEditor({
  initial,
}: {
  initial: TokenUsagePricingConfigResponse
}) {
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
      setError(errorMessage(err))
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
    >
      <div className={styles.main}>
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
        <ModelPricingSection
          currency={currency}
          rows={rows}
          onCurrencyChange={setCurrency}
          onRowChange={(index, patch) =>
            setRows((prev) =>
              prev.map((row, i) => (i === index ? { ...row, ...patch } : row))
            )
          }
          onAddRow={() => setRows((prev) => [...prev, { ...EMPTY_ROW }])}
          onRemoveRow={(index) =>
            setRows((prev) => prev.filter((_, i) => i !== index))
          }
        />
        <InstanceSettingsSection />
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
