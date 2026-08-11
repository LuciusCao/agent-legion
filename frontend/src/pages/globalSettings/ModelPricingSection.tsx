import styles from '../GlobalSettingsPage.module.css'

export interface RateRow {
  provider: string
  model: string
  input_per_1m: string
  output_per_1m: string
  cache_read_per_1m: string
}

export const EMPTY_ROW: RateRow = {
  provider: '',
  model: '',
  input_per_1m: '',
  output_per_1m: '',
  cache_read_per_1m: '',
}

interface ModelPricingSectionProps {
  currency: string
  rows: RateRow[]
  onCurrencyChange: (value: string) => void
  onRowChange: (index: number, patch: Partial<RateRow>) => void
  onAddRow: () => void
  onRemoveRow: (index: number) => void
}

export function ModelPricingSection({
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
