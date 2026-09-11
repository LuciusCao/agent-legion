// 实例设置表单字段元数据的共享类型（从 instanceSettingsFields.ts 拆出，
// 让主表与保留策略姊妹表共享同一组形状定义；#591 起跨组共享的开关条目
// 本体也落在这里——字段组表们已贴预算墙，组表只登记引用）。

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

// #591：result 终态事务批量化 kill-switch 的表单开关项（关闭后每个
// 完成写走直连串行路径，0.7.9 行为；重启生效）。
export const BATCHING_TOGGLE: ToggleDef = {
  path: 'agent_workers.result_commit_batching',
  label: 'result 终态事务批量化',
}
