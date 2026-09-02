import { useState } from 'react'
import { useUiStore } from '../../stores/uiStore'
import { updateTokenUsagePricing } from '../../api/tokenUsagePricing'
import type { TokenUsagePricingConfigResponse } from '../../api/tokenUsagePricing'
import { toErrorMessage } from '../../lib/queryError'
import { EMPTY_ROW, ModelPricingSection } from './ModelPricingSection'
import type { RateRow } from './ModelPricingSection'
import { serialize, toRows } from './pricingRows'
import styles from '../GlobalSettingsPage.module.css'

// 模型定价的自包含卡片：编辑态、校验与保存都在卡片内完成（与其他
// 设置卡片一致），不再依赖页面级 AppBar 保存按钮。
function buildPayload(currency: string, rows: RateRow[]) {
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

export function ModelPricingCard({
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

  async function handleSave() {
    setError('')
    let payload
    try {
      payload = buildPayload(currency, rows)
    } catch (err) {
      setError(toErrorMessage(err))
      return
    }
    setSaving(true)
    try {
      const result = await updateTokenUsagePricing(payload)
      setBaseline(serialize(result.currency, toRows(result.pricing)))
      useUiStore.getState().showToast('模型定价已保存', 'success')
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={styles.card}>
      <h3 className={styles.heading}>模型定价</h3>
      <p className={styles.hint}>
        按 provider + model 配置每百万 token 价格；历史 run 按各自使用的
        provider + model 匹配价格分别计费，用于统计各 workspace 的成本消耗。
      </p>
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
      <div className={styles.row}>
        <button
          type="button"
          className={styles.textButton}
          disabled={!isDirty || saving}
          aria-label="保存模型定价"
          onClick={() => void handleSave()}
        >
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </div>
  )
}
