import React, { useEffect, useMemo, useState } from 'react'
import {
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Chip,
  Button,
} from '@mui/material'
import { fetchWorkspaceTokenUsage } from '../api/tokenUsage'
import type { TokenUsageWorkspaceResponse } from '../api/tokenUsage'
import { MaterialIcon } from './MaterialIcon'
import styles from './TokenUsagePanel.module.css'

type GroupBy = 'node' | 'model' | 'skill_version' | 'node_skill_version'

interface FilterState {
  nodeKey: string
  model: string
  skillVersion: string
}

const GROUPS: { key: GroupBy; label: string }[] = [
  { key: 'node', label: '按节点' },
  { key: 'model', label: '按模型' },
  { key: 'skill_version', label: '按技能版本' },
  { key: 'node_skill_version', label: '节点 + 技能版本' },
]

function fmt(value: number | null | undefined) {
  return typeof value === 'number' ? value.toLocaleString('zh-CN') : '-'
}

function money(currency: string, value: number | null | undefined) {
  if (typeof value !== 'number') return '未配置价格'
  const symbol = currency === 'CNY' ? '¥' : currency
  return `${symbol} ${value.toFixed(4)}`
}

function formatCoverage(value: number | null | undefined) {
  if (typeof value !== 'number') return '-'
  return `${Math.round(value * 100)}%`
}

export function TokenUsagePanel({ workspaceId }: { workspaceId: string }) {
  const [groupBy, setGroupBy] = useState<GroupBy>('node')
  const [filters, setFilters] = useState<FilterState>({
    nodeKey: '',
    model: '',
    skillVersion: '',
  })
  const [data, setData] = useState<TokenUsageWorkspaceResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset expansion when view changes
    setExpanded(new Set())
  }, [workspaceId, groupBy, filters])

  useEffect(() => {
    const params = new URLSearchParams({ group_by: groupBy })
    if (filters.nodeKey) params.set('node_key', filters.nodeKey)
    if (filters.model) params.set('model', filters.model)
    if (filters.skillVersion) params.set('skill_version', filters.skillVersion)

    let stale = false
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loading state tied to fetch lifecycle
    setLoading(true)
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setError(null)
    fetchWorkspaceTokenUsage(workspaceId, params)
      .then((next) => {
        if (!stale) setData(next)
      })
      .catch((err) => {
        if (!stale) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!stale) setLoading(false)
      })
    return () => {
      stale = true
    }
  }, [workspaceId, groupBy, filters])

  const summary = data?.summary as
    | {
        total_tokens?: number
        input_tokens?: number
        output_tokens?: number
        cache_read_tokens?: number
        cost?: {
          total?: number | null
          currency?: string
          pricing_missing?: boolean
        }
      }
    | undefined

  const totalRuns = useMemo(() => {
    return (data?.runs_with_usage ?? 0) + (data?.runs_without_usage ?? 0)
  }, [data])

  const coverage = useMemo(() => {
    return totalRuns
      ? Math.round(((data?.runs_with_usage ?? 0) / totalRuns) * 100)
      : 0
  }, [data, totalRuns])

  const avgTokensPerRun = useMemo(() => {
    const runs = data?.runs_with_usage ?? 0
    const tokens = summary?.total_tokens ?? 0
    return runs ? Math.round(tokens / runs) : 0
  }, [summary, data])

  const highestCostGroup = useMemo(() => {
    if (!data?.groups.length) return null
    return data.groups.reduce((max, group) =>
      group.total_cost > max.total_cost ? group : max
    )
  }, [data])

  const filterOptions = useMemo(() => {
    const nodes = new Set<string>()
    const models = new Set<string>()
    const versions = new Set<string>()
    for (const group of data?.groups ?? []) {
      if (group.node_key) nodes.add(group.node_key)
      if (group.model) models.add(group.model)
      if (group.skill_version) versions.add(group.skill_version)
    }
    return {
      nodes: Array.from(nodes).sort(),
      models: Array.from(models).sort(),
      versions: Array.from(versions).sort(),
    }
  }, [data])

  const activeFilters = useMemo(() => {
    const result: { label: string; onDelete: () => void }[] = []
    if (filters.nodeKey) {
      result.push({
        label: `节点: ${filters.nodeKey}`,
        onDelete: () => setFilters((f) => ({ ...f, nodeKey: '' })),
      })
    }
    if (filters.model) {
      result.push({
        label: `模型: ${filters.model}`,
        onDelete: () => setFilters((f) => ({ ...f, model: '' })),
      })
    }
    if (filters.skillVersion) {
      result.push({
        label: `技能版本: ${filters.skillVersion}`,
        onDelete: () => setFilters((f) => ({ ...f, skillVersion: '' })),
      })
    }
    return result
  }, [filters])

  const toggleExpanded = (groupKey: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(groupKey)) {
        next.delete(groupKey)
      } else {
        next.add(groupKey)
      }
      return next
    })
  }

  if (error) {
    return <p className={styles.error}>Token 统计加载失败：{error}</p>
  }

  return (
    <section className={styles.panel} aria-label="Workspace Token 使用分析">
      <header className={styles.header}>
        <div className={styles.titleGroup}>
          <h2>Token 使用分析</h2>
          <p>按节点、模型、技能版本比较 token 和成本。</p>
        </div>
        <Chip
          label={
            data
              ? `${data.runs_with_usage}/${totalRuns} runs have usage`
              : '加载中'
          }
          color="primary"
          size="small"
          variant="outlined"
        />
      </header>

      <div className={styles.summaryGrid}>
        <div className={styles.metric}>
          <div className={styles.metricLabel}>总 Token</div>
          <div
            className={styles.metricValue}
            data-testid="total-tokens-summary"
          >
            {fmt(summary?.total_tokens)}
          </div>
          <div className={styles.metricMeta}>
            输入 {fmt(summary?.input_tokens)} / 输出{' '}
            {fmt(summary?.output_tokens)} / 缓存{' '}
            {fmt(summary?.cache_read_tokens)}
          </div>
        </div>
        <div className={styles.metric}>
          <div className={styles.metricLabel}>总成本</div>
          <div className={styles.metricValue} data-testid="total-cost-summary">
            {money(data?.currency ?? 'CNY', summary?.cost?.total ?? undefined)}
          </div>
          <div className={styles.metricMeta}>
            {summary?.cost?.pricing_missing ? '缺少定价配置' : '按配置单价计算'}
          </div>
        </div>
        <div className={styles.metric}>
          <div className={styles.metricLabel}>平均每 run</div>
          <div className={styles.metricValue}>{fmt(avgTokensPerRun)}</div>
          <div className={styles.metricMeta}>
            仅统计有 usage 的 {data?.runs_with_usage ?? 0} 次 run
          </div>
        </div>
        <div className={styles.metric}>
          <div className={styles.metricLabel}>最高成本节点</div>
          <div className={styles.metricValue}>
            {highestCostGroup?.group_key || '-'}
          </div>
          <div className={styles.metricMeta}>
            {highestCostGroup
              ? `${money(data?.currency ?? 'CNY', highestCostGroup.total_cost)} / ${highestCostGroup.runs} runs`
              : '-'}
          </div>
        </div>
        <div className={styles.metric}>
          <div className={styles.metricLabel}>覆盖率</div>
          <div className={styles.metricValue} data-testid="coverage-summary">
            <span>{coverage}</span>
            <span>%</span>
          </div>
          <div className={styles.metricMeta}>
            {data?.runs_without_usage ?? 0} 个 run 无 token 数据
          </div>
          <div className={styles.usageBar}>
            <span style={{ width: `${coverage}%` }} />
          </div>
        </div>
      </div>

      <div className={styles.controls}>
        <div className={styles.tabs} role="tablist" aria-label="聚合维度">
          {GROUPS.map((group) => (
            <button
              key={group.key}
              type="button"
              role="tab"
              aria-selected={group.key === groupBy}
              className={group.key === groupBy ? styles.activeTab : styles.tab}
              onClick={() => setGroupBy(group.key)}
            >
              {group.label}
            </button>
          ))}
        </div>

        <div className={styles.filters}>
          <FormControl size="small" className={styles.filterControl}>
            <InputLabel id="token-usage-node-filter-label">节点</InputLabel>
            <Select
              labelId="token-usage-node-filter-label"
              value={filters.nodeKey}
              label="节点"
              onChange={(e) =>
                setFilters((f) => ({ ...f, nodeKey: e.target.value }))
              }
            >
              <MenuItem value="">全部节点</MenuItem>
              {filterOptions.nodes.map((node) => (
                <MenuItem key={node} value={node}>
                  {node}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <FormControl size="small" className={styles.filterControl}>
            <InputLabel id="token-usage-model-filter-label">模型</InputLabel>
            <Select
              labelId="token-usage-model-filter-label"
              value={filters.model}
              label="模型"
              onChange={(e) =>
                setFilters((f) => ({ ...f, model: e.target.value }))
              }
            >
              <MenuItem value="">全部模型</MenuItem>
              {filterOptions.models.map((model) => (
                <MenuItem key={model} value={model}>
                  {model}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <FormControl size="small" className={styles.filterControl}>
            <InputLabel id="token-usage-version-filter-label">
              技能版本
            </InputLabel>
            <Select
              labelId="token-usage-version-filter-label"
              value={filters.skillVersion}
              label="技能版本"
              onChange={(e) =>
                setFilters((f) => ({ ...f, skillVersion: e.target.value }))
              }
            >
              <MenuItem value="">全部版本</MenuItem>
              {filterOptions.versions.map((version) => (
                <MenuItem key={version} value={version}>
                  {version}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </div>
      </div>

      {activeFilters.length > 0 && (
        <div className={styles.activeFilters}>
          {activeFilters.map((filter) => (
            <Chip
              key={filter.label}
              label={filter.label}
              onDelete={filter.onDelete}
              size="small"
            />
          ))}
        </div>
      )}

      <div className={styles.tableWrap}>
        <table aria-label="Token 聚合表">
          <thead>
            <tr>
              <th>维度</th>
              <th>Runs</th>
              <th>Avg Input</th>
              <th>Avg Output</th>
              <th>Avg Cache</th>
              <th>Total Tokens</th>
              <th>Total Cost</th>
              <th>Avg Cost</th>
              <th>覆盖率</th>
              <th>明细</th>
            </tr>
          </thead>
          <tbody>
            {(data?.groups ?? []).map((group) => {
              const isExpanded = expanded.has(group.group_key)
              return (
                <React.Fragment key={group.group_key}>
                  <tr>
                    <td>
                      <div className={styles.dimensionCell}>
                        <span className={styles.dimensionName}>
                          {group.group_key || 'unknown'}
                        </span>
                        <span className={styles.dimensionMeta}>
                          {group.provider && `${group.provider} / `}
                          {group.model}
                          {group.skill_version && ` / ${group.skill_version}`}
                        </span>
                      </div>
                    </td>
                    <td>{group.runs}</td>
                    <td>{fmt(Math.round(group.avg_input_tokens))}</td>
                    <td>{fmt(Math.round(group.avg_output_tokens))}</td>
                    <td>{fmt(Math.round(group.avg_cache_read_tokens))}</td>
                    <td>{fmt(group.total_tokens)}</td>
                    <td className={styles.money}>
                      {money(data?.currency ?? 'CNY', group.total_cost)}
                    </td>
                    <td>{money(data?.currency ?? 'CNY', group.avg_cost)}</td>
                    <td>
                      <div className={styles.coverage}>
                        <span>{formatCoverage(group.coverage)}</span>
                        <div className={styles.miniBar}>
                          <span
                            style={{
                              width: `${Math.round((group.coverage ?? 0) * 100)}%`,
                            }}
                          />
                        </div>
                      </div>
                    </td>
                    <td>
                      <Button
                        size="small"
                        onClick={() => toggleExpanded(group.group_key)}
                        endIcon={
                          <MaterialIcon
                            name={isExpanded ? 'expand_less' : 'expand_more'}
                          />
                        }
                      >
                        {isExpanded ? '收起' : '展开'}
                      </Button>
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr key={`${group.group_key}-detail`}>
                      <td colSpan={10} className={styles.detailCell}>
                        <div className={styles.detailBox}>
                          <div className={styles.breakdownGrid}>
                            <div>
                              <strong>Token 明细</strong>
                              <div className={styles.breakdownRow}>
                                <span>Input</span>
                                <div className={styles.barTrack}>
                                  <span
                                    style={{
                                      width: `${group.total_tokens ? (group.total_input_tokens / group.total_tokens) * 100 : 0}%`,
                                    }}
                                  />
                                </div>
                                <span>{fmt(group.total_input_tokens)}</span>
                              </div>
                              <div className={styles.breakdownRow}>
                                <span>Output</span>
                                <div
                                  className={`${styles.barTrack} ${styles.outputBar}`}
                                >
                                  <span
                                    style={{
                                      width: `${group.total_tokens ? (group.total_output_tokens / group.total_tokens) * 100 : 0}%`,
                                    }}
                                  />
                                </div>
                                <span>{fmt(group.total_output_tokens)}</span>
                              </div>
                              <div className={styles.breakdownRow}>
                                <span>Cache</span>
                                <div
                                  className={`${styles.barTrack} ${styles.cacheBar}`}
                                >
                                  <span
                                    style={{
                                      width: `${group.total_tokens ? (group.total_cache_read_tokens / group.total_tokens) * 100 : 0}%`,
                                    }}
                                  />
                                </div>
                                <span>
                                  {fmt(group.total_cache_read_tokens)}
                                </span>
                              </div>
                            </div>
                            <div>
                              <strong>成本明细</strong>
                              <div className={styles.costRow}>
                                <span>总成本</span>
                                <span>
                                  {money(
                                    data?.currency ?? 'CNY',
                                    group.total_cost
                                  )}
                                </span>
                              </div>
                              <div className={styles.costRow}>
                                <span>平均成本</span>
                                <span>
                                  {money(
                                    data?.currency ?? 'CNY',
                                    group.avg_cost
                                  )}
                                </span>
                              </div>
                              {group.pricing_missing && (
                                <div className={styles.pricingMissing}>
                                  缺少定价配置，费用为估算值
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
            {!loading && data && data.groups.length === 0 && (
              <tr>
                <td colSpan={10} className={styles.emptyCell}>
                  暂无 token 统计
                </td>
              </tr>
            )}
            {loading && (
              <tr>
                <td colSpan={10} className={styles.emptyCell}>
                  加载中…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
