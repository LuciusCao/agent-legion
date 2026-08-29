import type { InstanceSettingsUpdate } from '../../api/instanceSettings'
import styles from '../GlobalSettingsPage.module.css'

export type InstanceFormValues = Record<string, string | boolean>

type OpenClawDoc = InstanceSettingsUpdate['openclaw']

export function openClawFormValues(doc: OpenClawDoc): InstanceFormValues {
  return {
    'openclaw.cwd': doc.cwd,
  }
}

export function buildOpenClawPayload(values: InstanceFormValues): OpenClawDoc {
  const cwd = String(values['openclaw.cwd'] ?? '').trim()
  if (!cwd) {
    throw new Error('OpenClaw 工作目录 不能为空')
  }
  return { cwd }
}

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
        <label className={styles.label} htmlFor="instance-openclaw.cwd">
          OpenClaw 工作目录
        </label>
        <input
          id="instance-openclaw.cwd"
          className={styles.currencyInput}
          type="text"
          value={String(values['openclaw.cwd'] ?? '')}
          onChange={(e) =>
            setValues((prev) => ({
              ...prev,
              'openclaw.cwd': e.target.value,
            }))
          }
        />
      </div>
    </div>
  )
}
