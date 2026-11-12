// 实例设置表单的字段组聚合与字段查找；各组定义位于同域姊妹模块，
// 用户视角说明在 instanceSettingsHints.ts。

import type { FieldGroup, NumberFieldDef } from './instanceSettingsFieldTypes'
import { CAPACITY_FIELD_GROUPS } from './instanceSettingsCapacityFields'
import { CORE_FIELD_GROUPS } from './instanceSettingsCoreFields'
import { RETENTION_FIELD_GROUPS } from './instanceSettingsRetentionFields'
import { SECURITY_FIELD_GROUPS } from './instanceSettingsSecurityFields'
import { WORKER_FIELD_GROUPS } from './instanceSettingsWorkerFields'
import { WORKFLOW_FIELD_GROUPS } from './instanceSettingsWorkflowFields'

export type { FieldGroup, NumberFieldDef } from './instanceSettingsFieldTypes'
export { CAPACITY_FIELD_GROUPS } from './instanceSettingsCapacityFields'
export { RETENTION_FIELD_GROUPS } from './instanceSettingsRetentionFields'
export { SECURITY_FIELD_GROUPS } from './instanceSettingsSecurityFields'

export const FIELD_GROUPS: FieldGroup[] = [
  ...CORE_FIELD_GROUPS,
  ...WORKFLOW_FIELD_GROUPS,
  ...RETENTION_FIELD_GROUPS,
  ...CAPACITY_FIELD_GROUPS,
  ...SECURITY_FIELD_GROUPS,
  ...WORKER_FIELD_GROUPS,
]

export function fieldDef(path: string): NumberFieldDef {
  const field = FIELD_GROUPS.flatMap((g) => g.fields).find(
    (f) => f.path === path
  )
  if (!field) throw new Error(`unknown instance settings field: ${path}`)
  return field
}
