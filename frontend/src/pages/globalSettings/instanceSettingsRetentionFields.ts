// 保留策略类字段组（材料 TTL #160、执行面行保留 #354）。
// 从 instanceSettingsFields.ts 拆出以控制体积预算；两者都是「0 = 关闭、
// 热读立即生效」的语义，表单渲染完全复用 FieldGroup。

import type { FieldGroup } from './instanceSettingsFieldTypes'

export const RETENTION_FIELD_GROUPS: FieldGroup[] = [
  {
    title: '材料',
    fields: [
      {
        path: 'materials_ttl_days',
        label: '材料保留天数（0 关闭）',
        integer: true,
        allowZero: true,
        max: 36500,
        hint: '保存后立即生效，无需重启',
      },
    ],
    toggles: [],
  },
  {
    title: '执行面保留',
    fields: [
      {
        path: 'execution_retention_days',
        label: '执行记录保留天数（0 关闭）',
        integer: true,
        allowZero: true,
        max: 36500,
        hint: '终态执行行（请求/租约/用量）按窗口删除；0 为不删除，保存后立即生效',
      },
    ],
    toggles: [],
  },
]
