import type { ConfigSchema } from '../../../types'
import { useSettingStore } from '../../../stores/settingStore'
import { parseConfigValue } from '../shared/workflowStudioYamlDraft.nodeConfig'
import { patchWorkflowNodeConfigValue } from '../shared/workflowStudioYamlDraft.nodeConfig'
import { isSecretConfigProperty } from '../shared/workflowStudioYamlDraft.configSchema.constraints'
import {
  ConfigValueField,
  configOverrideValueOf,
  configFieldRaw,
} from './ConfigValueField'
import styles from './WorkflowStructuredEditor.module.css'

type Props = {
  nodeKey: string
  schema: ConfigSchema
  config: Record<string, unknown>
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// code 节点 revision 作用域的 config 版本值表单（#418 后半）：按草稿
// config_schema 的属性生成控件，值写 draft YAML 的 node config，随发布
// 进入新版本。未填写的键沿用 Schema 默认值。从 WorkflowNodeConfigSection
// 拆出守单文件预算；单字段控件拆在 ConfigValueField（含 enum/边界
// 校验），遮蔽徽标拆在 ConfigValueFieldLabel，失焦提交输入框拆在
// NumberOrTextValueField。
// - secret 属性（#428 codex P1-A）永不渲染输入框：draft 保存不经过
//   settings PATCH 的脱敏通道，明文会进 revision 与 intake 冻结数据
//   （VAULT-SECRET-001）；改走下方运行时覆盖通道（vault 落库）。
// - enum 属性用下拉、minimum/maximum 在失焦提交时校验（P1-B）：发布
//   只校验 schema 不校验 config，enum 外/越界值进 active revision 后
//   所有新 job 的 intake 都会失败。
export function WorkflowNodeConfigValues({
  nodeKey,
  schema,
  config,
  definitionYaml,
  setDefinitionYaml,
  readOnly,
}: Props) {
  const liveOverrides = useSettingStore((s) => s.settings.nodeConfig?.[nodeKey])
  const keys = Object.keys(schema.properties ?? {}).filter((key) => {
    const prop = schema.properties?.[key]
    return prop != null && typeof prop === 'object'
  })
  const patchValue = (key: string, raw: string) => {
    const prop = schema.properties?.[key]
    if (!prop) return
    try {
      setDefinitionYaml(
        patchWorkflowNodeConfigValue(
          definitionYaml,
          nodeKey,
          key,
          parseConfigValue(raw, prop)
        )
      )
    } catch {
      // 非法输入不落草稿；受控输入回弹。
    }
  }

  const hasOverride = keys.some((key) => key in (liveOverrides ?? {}))
  const secretKeys = keys.filter((key) =>
    isSecretConfigProperty(schema.properties![key])
  )
  return (
    <>
      <p className={styles.fieldHint}>
        版本值：写入 workflow 定义，随发布进入新版本；job 在 intake 时
        按此值冻结。未填写的键沿用 Schema 默认值。
        {hasOverride
          ? ' 标注「已被运行时覆盖」的键例外：workspace 覆盖优先级更高，intake 实际生效的是覆盖值。'
          : ''}
      </p>
      {secretKeys.length > 0 && (
        <p className={styles.fieldHint}>
          敏感属性（{secretKeys.join('、')}）不在此编辑：secret 值必须经 vault
          加密落库，请通过下方「运行时覆盖」通道设置。
        </p>
      )}
      <div className={styles.fieldStack}>
        {keys.map((key) => {
          const prop = schema.properties![key]
          if (isSecretConfigProperty(prop)) return null
          return (
            <ConfigValueField
              key={key}
              fieldKey={key}
              prop={prop}
              raw={configFieldRaw(config, key)}
              overrideValue={configOverrideValueOf(liveOverrides, key)}
              readOnly={readOnly}
              onCommit={(next) => patchValue(key, next)}
            />
          )
        })}
      </div>
    </>
  )
}
