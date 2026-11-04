// 工作流提交与宿主本地执行的字段组。

import type { FieldGroup } from './instanceSettingsFieldTypes'

export const WORKFLOW_FIELD_GROUPS: FieldGroup[] = [
  {
    title: '运行与本地执行',
    fields: [
      // #358：单次 run 条目上限（0 不限制），超限提交被 API 拒绝。
      // workflows.enabled 已随 #385/#389 退役（部署形态由 code_capacity 表达）。
      {
        path: 'workflows.max_items_per_run',
        label: '单次 run 条目上限（0 不限制）',
        integer: true,
        allowZero: true,
      },
      // #786：节点代码体积上限纳入实例设置（#628 的 env-only 决策改判）。
      // 验收反馈：表单单位 KB（契约仍是字节，换算在 formValues/payload 两侧，
      // 与 WorkflowNodeCodeEditor 的 Math.round(bytes/1024) KB 展示同源）。
      // min 以显示单位计：1 KB 对应契约 ge=1024 字节。
      {
        path: 'workflows.node_code_max_bytes',
        label: '节点代码体积上限（KB）',
        integer: true,
        min: 1,
      },
      // #389：code_capacity 是实例级本地兜底执行并发上限，重启生效；
      // 0 = 纯远程模式（不在宿主本地执行 code 节点）。
      {
        path: 'code_capacity',
        label: '本地执行并发上限（0 = 纯远程模式）',
        integer: true,
        allowZero: true,
      },
    ],
    toggles: [],
  },
]
