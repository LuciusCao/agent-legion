// 实例设置表单字段元数据的共享类型（从 instanceSettingsFields.ts 拆出，
// 让主表与保留策略姊妹表共享同一组形状定义）。

export interface NumberFieldDef {
  path: string
  label: string
  integer: boolean
  // 允许 0（语义为「关闭」的字段，如材料 TTL）；缺省要求 > 0。
  allowZero?: boolean
  // input 的 max 属性（与后端契约上界一致）；缺省不设。
  max?: number
  // 字段级提示，覆盖卡片顶部的统一文案（如热读字段无需重启）。
  hint?: string
}

export interface ToggleDef {
  path: string
  label: string
}

export interface FieldGroup {
  title: string
  fields: NumberFieldDef[]
  toggles: ToggleDef[]
}
