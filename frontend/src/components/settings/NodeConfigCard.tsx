import { useState } from 'react'
import { Button } from '@mui/material'
import { api } from '../../api'
import { useSettingStore } from '../../stores/settingStore'
import { useUiStore } from '../../stores/uiStore'
import type { ConfigSchema, WorkspaceSettings } from '../../types'
import { RuntimeMutableBadges } from './RuntimeMutableBadges'
import { SchemaConfigForm } from './SchemaConfigForm'

type SettingsResponse = { settings?: Partial<WorkspaceSettings> }

// Merge the server-returned node-config keys into the setting store without
// clobbering unrelated unsaved edits, and keep originalSettings in sync so
// the page-level dirty flag is unaffected by a successful node save.
export function applySavedSettings(saved: Partial<WorkspaceSettings>) {
  const state = useSettingStore.getState()
  const merge = (base: WorkspaceSettings | null): WorkspaceSettings | null =>
    base
      ? {
          ...base,
          nodeConfig: saved.nodeConfig ?? base.nodeConfig,
          nodeConfigSchemas: saved.nodeConfigSchemas ?? base.nodeConfigSchemas,
        }
      : base
  useSettingStore.setState({
    settings: merge(state.settings) as WorkspaceSettings,
    originalSettings: merge(state.originalSettings),
  })
}

interface CardProps {
  workspaceId: string
  nodeKey: string
  label: string
  schema: ConfigSchema
  initialValues: Record<string, unknown>
}

export function NodeConfigCard({
  workspaceId,
  nodeKey,
  label,
  schema,
  initialValues,
}: CardProps) {
  const [values, setValues] = useState<Record<string, unknown>>(initialValues)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const save = async (next: Record<string, unknown>) => {
    setSaving(true)
    setError('')
    try {
      const result = await api<SettingsResponse>(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/settings/nodes`,
        {
          method: 'PATCH',
          body: JSON.stringify({ nodeConfig: { [nodeKey]: next } }),
        }
      )
      if (result.settings) {
        applySavedSettings(result.settings)
        const savedValues = result.settings.nodeConfig?.[nodeKey]
        setValues(savedValues ?? next)
      }
      useUiStore.getState().showToast('节点配置已保存', 'success')
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      style={{
        border: '1px solid #e0e0e0',
        borderRadius: 12,
        padding: 16,
      }}
    >
      <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 4 }}>
        {label}
      </div>
      {label !== nodeKey && (
        <div style={{ fontSize: 12, color: '#616161', marginBottom: 12 }}>
          {nodeKey}
        </div>
      )}
      <RuntimeMutableBadges schema={schema} />
      <SchemaConfigForm
        schema={schema}
        values={values}
        onChange={setValues}
        disabled={saving}
      />
      <div style={{ display: 'flex', gap: 12, marginTop: 12 }}>
        <Button
          variant="outlined"
          onClick={() => void save(values)}
          disabled={saving}
        >
          {saving ? '保存中...' : '保存'}
        </Button>
        <Button variant="text" onClick={() => void save({})} disabled={saving}>
          清除覆盖
        </Button>
      </div>
      {error && (
        <div role="alert" style={{ color: '#d32f2f', marginTop: 12 }}>
          {error}
        </div>
      )}
    </div>
  )
}
