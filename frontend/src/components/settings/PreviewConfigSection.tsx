/**
 * 设置页的产物预览配置 section（issue #11 第 3 层）。
 *
 * 勾选来源 = 当前 workflow 定义里各节点声明的 outputs（按节点分组），
 * 隐藏列表存 workspace 级 previewHidden（与 job 页勾选菜单同一份配置）。
 * 遵循设置页 draft 语义：勾选进 settings.previewHidden，统一走 saveAll
 * （PUT /configuration）保存。
 */
import { Checkbox } from '@mui/material'
import pageStyles from '../../pages/SettingsPage.module.css'
import styles from './PreviewConfigSection.module.css'
import type { WorkflowDefinitionRecord, WorkspaceSettings } from '../../types'

interface Props {
  settings: WorkspaceSettings
  workflowDefinition: WorkflowDefinitionRecord | null
  setSettings: (s: Partial<WorkspaceSettings>) => void
}

export function PreviewConfigSection({
  settings,
  workflowDefinition,
  setSettings,
}: Props) {
  const hidden = settings.previewHidden ?? []
  const hiddenSet = new Set(hidden)

  const toggle = (artifactName: string, visible: boolean) => {
    const nextHidden = visible
      ? hidden.filter((name) => name !== artifactName)
      : Array.from(new Set([...hidden, artifactName])).sort()
    setSettings({ previewHidden: nextHidden })
  }

  // 按节点分组列出全部声明产物；workflow 定义未加载时提示。
  const nodeGroups = (workflowDefinition?.nodes ?? [])
    .map((node) => ({
      key: node.key,
      label: node.label || node.key,
      outputs: node.outputs ?? [],
    }))
    .filter((group) => group.outputs.length > 0)

  return (
    <section id="preview-config" className={pageStyles.section}>
      <h2 className={pageStyles.sectionTitle}>产物预览</h2>
      <hr className={pageStyles.sectionDivider} />
      <span className={styles.hint}>
        勾选在任务详情左栏显示的产物文件（对该工作区的所有任务生效）；不勾选的产物仍可在
        「产物文件」弹窗中查看。工作流升级产生的新产物默认显示。
      </span>
      {nodeGroups.length === 0 ? (
        <div className={styles.empty}>当前工作流未声明产物文件。</div>
      ) : (
        <div className={styles.groups}>
          {nodeGroups.map((group) => (
            <div key={group.key} className={styles.group}>
              <span className={styles.groupLabel}>{group.label}</span>
              <div className={styles.outputs}>
                {group.outputs.map((name) => {
                  const isChecked = !hiddenSet.has(name)
                  return (
                    <div
                      key={`${group.key}:${name}`}
                      className={styles.outputRow}
                    >
                      <Checkbox
                        checked={isChecked}
                        onChange={() => toggle(name, !isChecked)}
                        size="small"
                      />
                      <span className={styles.outputName}>{name}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
