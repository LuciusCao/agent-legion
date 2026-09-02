import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { useUiStore } from '../../stores/uiStore'
import {
  getInstanceSettings,
  updateInstanceSettings,
} from '../../api/instanceSettings'
import type { InstanceSettingsResponse } from '../../api/instanceSettings'
import { FIELD_GROUPS, RETENTION_FIELD_GROUPS } from './instanceSettingsFields'
import { GROUP_HINTS } from './instanceSettingsHints'
import {
  buildPayload,
  FieldGroupFields,
  toFormValues,
} from './instanceSettingsForm'
import type { FormValues } from './instanceSettingsForm'
import styles from '../GlobalSettingsPage.module.css'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

// 保留策略组（材料 TTL、执行面保留——均为热读立即生效的业务参数）直接
// 展示；其余调优参数默认折叠进「高级参数」，排障或容量调优时再展开。
const RETENTION_TITLES = new Set(RETENTION_FIELD_GROUPS.map((g) => g.title))
const VISIBLE_GROUPS = FIELD_GROUPS.filter((g) => RETENTION_TITLES.has(g.title))
const ADVANCED_GROUPS = FIELD_GROUPS.filter(
  (g) => !RETENTION_TITLES.has(g.title)
)

function InstanceSettingsEditor({
  initial,
}: {
  initial: InstanceSettingsResponse
}) {
  const queryClient = useQueryClient()
  const [values, setValues] = useState<FormValues>(() => toFormValues(initial))
  const [baseline, setBaseline] = useState(() =>
    JSON.stringify(toFormValues(initial))
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const isDirty = JSON.stringify(values) !== baseline

  function setValue(path: string, value: string | boolean) {
    setValues((prev) => ({ ...prev, [path]: value }))
  }

  async function handleSave() {
    setError('')
    setSaving(true)
    try {
      const result = await updateInstanceSettings(buildPayload(values))
      const next = toFormValues(result)
      setValues(next)
      setBaseline(JSON.stringify(next))
      // 同步 query cache：保存后 30s 内重进页面不得回显旧值（staleTime 窗口）。
      queryClient.setQueryData(extraQueryKeys.instanceSettings(), result)
      useUiStore
        .getState()
        .showToast('实例设置已保存，除保留策略外需重启生效', 'success')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  function renderGroup(group: (typeof FIELD_GROUPS)[number]) {
    return (
      <div key={group.title}>
        <p className={styles.groupTitle}>{group.title}</p>
        {GROUP_HINTS[group.title] && (
          <p className={styles.hint}>{GROUP_HINTS[group.title]}</p>
        )}
        <FieldGroupFields group={group} values={values} onChange={setValue} />
      </div>
    )
  }

  return (
    <div className={styles.card}>
      <h3 className={styles.heading}>实例设置</h3>
      <p className={styles.hint}>
        默认值适用于绝大多数部署，仅在排障或容量调优时调整。除材料与执行面
        保留期外，保存后需重启服务才能生效。
      </p>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {VISIBLE_GROUPS.map(renderGroup)}
      <button
        type="button"
        className={styles.textButton}
        aria-expanded={advancedOpen}
        onClick={() => setAdvancedOpen((prev) => !prev)}
      >
        {advancedOpen ? '收起高级参数' : '展开高级参数'}
      </button>
      {advancedOpen && ADVANCED_GROUPS.map(renderGroup)}
      <div className={styles.row}>
        <span className={styles.label}>Skill 根目录</span>
        <code>{initial.skills_root}</code>
        <span className={styles.hint}>
          暂不支持修改；workspace 技能默认位于{' '}
          {`${initial.skills_root}/<workspace>/`}
        </span>
      </div>
      <button
        type="button"
        className={styles.textButton}
        onClick={() => void handleSave()}
        disabled={!isDirty || saving}
      >
        {saving ? '保存中…' : '保存实例设置'}
      </button>
    </div>
  )
}

export function InstanceSettingsSection() {
  const { data, error: loadQueryError } = useQuery({
    queryKey: extraQueryKeys.instanceSettings(),
    queryFn: getInstanceSettings,
  })
  const loadError = toErrorMessage(loadQueryError)

  if (loadError) {
    return (
      <div className={styles.card}>
        <h3 className={styles.heading}>实例设置</h3>
        <p className={styles.error} role="alert">
          {loadError}
        </p>
      </div>
    )
  }

  if (!data) return null

  return <InstanceSettingsEditor initial={data} />
}
