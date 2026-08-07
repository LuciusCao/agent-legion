import { FormControlLabel, Switch, TextField } from '@mui/material'
import type { InstanceSettingsUpdate } from '../../api/instanceSettings'
import styles from '../GlobalSettingsPage.module.css'

export type InstanceFormValues = Record<string, string | boolean>

type OpenClawDoc = InstanceSettingsUpdate['openclaw']

export function openClawFormValues(doc: OpenClawDoc): InstanceFormValues {
  return {
    'openclaw.cwd': doc.cwd,
    'openclaw.timeout_seconds': String(doc.timeout_seconds),
    'openclaw.isolated_workspace_root': doc.isolated_workspace_root,
    'openclaw.command_template': JSON.stringify(doc.command_template, null, 2),
    'openclaw.skill_safety.enabled': doc.skill_safety.enabled,
    'openclaw.skill_safety.repos': JSON.stringify(
      doc.skill_safety.repos,
      null,
      2
    ),
  }
}

function parseJson(
  values: InstanceFormValues,
  path: string,
  label: string
): unknown {
  const raw = String(values[path] ?? '').trim()
  try {
    return JSON.parse(raw)
  } catch {
    throw new Error(`${label} 不是合法 JSON`)
  }
}

function parseCommandTemplate(values: InstanceFormValues): string[] {
  const parsed = parseJson(values, 'openclaw.command_template', '命令模板')
  if (
    !Array.isArray(parsed) ||
    parsed.length === 0 ||
    parsed.some((item) => typeof item !== 'string' || item === '')
  ) {
    throw new Error('命令模板 必须是非空字符串数组')
  }
  return parsed
}

function parseSkillSafetyRepos(values: InstanceFormValues): { path: string }[] {
  const parsed = parseJson(
    values,
    'openclaw.skill_safety.repos',
    'skill_safety repos'
  )
  if (!Array.isArray(parsed)) {
    throw new Error('skill_safety repos 必须是数组')
  }
  for (const item of parsed) {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) {
      throw new Error('skill_safety repos 元素必须是对象')
    }
    const keys = Object.keys(item)
    // G3: refs are pinned by config/skills.lock; only `path` is allowed.
    if (keys.length !== 1 || keys[0] !== 'path') {
      throw new Error(
        'skill_safety repos 元素只允许 path 键（ref 由 skills.lock 钉死）'
      )
    }
    const path = (item as { path: unknown }).path
    if (typeof path !== 'string' || path === '') {
      throw new Error('skill_safety repos 的 path 必须是非空字符串')
    }
  }
  return parsed as { path: string }[]
}

function parseTimeout(values: InstanceFormValues): number {
  const raw = String(values['openclaw.timeout_seconds'] ?? '').trim()
  const parsed = Number(raw)
  if (!raw || !Number.isFinite(parsed) || parsed < 1) {
    throw new Error('OpenClaw 超时（秒） 必须是不小于 1 的整数')
  }
  return Math.round(parsed)
}

export function buildOpenClawPayload(values: InstanceFormValues): OpenClawDoc {
  const cwd = String(values['openclaw.cwd'] ?? '').trim()
  if (!cwd) {
    throw new Error('OpenClaw 工作目录 不能为空')
  }
  return {
    cwd,
    timeout_seconds: parseTimeout(values),
    isolated_workspace_root: String(
      values['openclaw.isolated_workspace_root'] ?? ''
    ).trim(),
    command_template: parseCommandTemplate(values),
    skill_safety: {
      enabled: Boolean(values['openclaw.skill_safety.enabled']),
      repos: parseSkillSafetyRepos(values),
    },
  }
}

interface TextRowDef {
  path: string
  label: string
}

const TEXT_ROWS: TextRowDef[] = [
  { path: 'openclaw.cwd', label: 'OpenClaw 工作目录' },
  { path: 'openclaw.isolated_workspace_root', label: '隔离工作区根目录' },
]

const JSON_ROWS: TextRowDef[] = [
  { path: 'openclaw.command_template', label: '命令模板（JSON 字符串数组）' },
  {
    path: 'openclaw.skill_safety.repos',
    label: 'skill_safety repos（JSON，仅 path 键）',
  },
]

export function OpenClawInstanceFields({
  values,
  setValues,
}: {
  values: InstanceFormValues
  setValues: React.Dispatch<React.SetStateAction<InstanceFormValues>>
}) {
  return (
    <div>
      <p className={styles.groupTitle}>OpenClaw</p>
      <div className={styles.row}>
        <label
          className={styles.label}
          htmlFor="instance-openclaw.timeout_seconds"
        >
          OpenClaw 超时（秒）
        </label>
        <input
          id="instance-openclaw.timeout_seconds"
          className={styles.currencyInput}
          type="number"
          min="1"
          step="1"
          value={String(values['openclaw.timeout_seconds'] ?? '')}
          onChange={(e) =>
            setValues((prev) => ({
              ...prev,
              'openclaw.timeout_seconds': e.target.value,
            }))
          }
        />
      </div>
      <div className={styles.row}>
        <FormControlLabel
          control={
            <Switch
              checked={Boolean(values['openclaw.skill_safety.enabled'])}
              onChange={(e) =>
                setValues((prev) => ({
                  ...prev,
                  'openclaw.skill_safety.enabled': e.target.checked,
                }))
              }
              inputProps={{ 'aria-label': '启用 skill 安全检查' }}
            />
          }
          label="启用 skill 安全检查"
        />
      </div>
      {TEXT_ROWS.map((field) => (
        <div className={styles.row} key={field.path}>
          <label className={styles.label} htmlFor={`instance-${field.path}`}>
            {field.label}
          </label>
          <input
            id={`instance-${field.path}`}
            className={styles.currencyInput}
            type="text"
            value={String(values[field.path] ?? '')}
            onChange={(e) =>
              setValues((prev) => ({
                ...prev,
                [field.path]: e.target.value,
              }))
            }
          />
        </div>
      ))}
      {JSON_ROWS.map((field) => (
        <div className={styles.row} key={field.path}>
          <TextField
            label={field.label}
            variant="outlined"
            value={String(values[field.path] ?? '')}
            onChange={(e) =>
              setValues((prev) => ({
                ...prev,
                [field.path]: e.target.value,
              }))
            }
            fullWidth
            multiline
            minRows={4}
          />
        </div>
      ))}
    </div>
  )
}
