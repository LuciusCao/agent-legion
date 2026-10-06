// 安全类开关组（#989 文档 CSP 兼容模式）。从 instanceSettingsFields.ts 拆出
// 以控制体积预算（也与保留策略姊妹表并列，减少多条线同改一张表）。与保留
// 策略组一样直接展示（非高级参数），无需重启；开关变化
// 时保存后整页刷新（CSP 头随 index.html 文档固定，客户端路由不会重读）。

import type { FieldGroup } from './instanceSettingsFieldTypes'

export const SECURITY_FIELD_GROUPS: FieldGroup[] = [
  {
    title: '安全',
    fields: [],
    toggles: [
      {
        path: 'csp_script_unsafe_inline',
        label: '预览面板兼容模式（允许内联事件属性）',
      },
    ],
  },
]
