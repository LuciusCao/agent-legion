import { useStudioStateOptional } from '../shared/studioStateContext'
import {
  combineYamlStructureRuntime,
  diffRuntimeSliceKeys,
  diffStructuralSummary,
  extractRuntimeSlice,
  type RuntimeSlice,
} from '../shared/workflowRuntimeSlice'
import styles from './StudioChatPanel.module.css'
import channelStyles from './WorkflowDraftCardChannels.module.css'

/** runtime diff 键在建议切片上的新值（diff 行「field → value」展示用）。 */
function runtimeValueAt(slice: RuntimeSlice, key: string): string {
  const parts = key.split('.')
  if (parts[0] === 'execution') {
    const field = parts[1] as keyof RuntimeSlice['execution']
    return slice.execution[field] || '（清除）'
  }
  const node = slice.nodes[parts[1]]
  if (!node) return ''
  const field = parts[2]
  if (field === 'tools') return node.tools.join(', ') || '（清除）'
  if (field === 'requires_labels') {
    if (parts.length > 3) {
      return node.requires_labels[parts.slice(3).join('.')] || '（清除）'
    }
    return (
      Object.entries(node.requires_labels)
        .map(([k, v]) => (v ? `${k}=${v}` : k))
        .join(', ') || '（清除）'
    )
  }
  const value =
    node[
      field as keyof Omit<
        RuntimeSlice['nodes'][string],
        'tools' | 'requires_labels'
      >
    ]
  return typeof value === 'string' && value ? value : '（清除）'
}

function formatRuntimeKey(key: string): string {
  return key.startsWith('nodes.')
    ? key.slice('nodes.'.length)
    : key.replace(/^execution\./, '顶层 ')
}

/**
 * M48 双通道分组（用户确认原型第 5 屏）：agent 的 workflow 建议按通道拆成
 * 「运行配置（应用并生效，不产生版本）」与「结构变更（存为草稿，随发布
 * 生效）」两组，分别提交、互不影响。无 studio 上下文（测试直渲染）或
 * 建议与现状无差异时不渲染（卡片退化为原单键应用）。
 */
export function WorkflowDraftCardChannelGroups(props: {
  draftYaml: string
  onApply: (yaml: string) => void
}) {
  const studio = useStudioStateOptional()
  const channels = (() => {
    if (!studio?.originalYaml) return null
    try {
      const proposal = extractRuntimeSlice(props.draftYaml)
      return {
        proposal,
        runtimeKeys: diffRuntimeSliceKeys(
          extractRuntimeSlice(studio.originalYaml),
          proposal
        ),
        structural: diffStructuralSummary(
          studio.draftYaml || studio.originalYaml,
          props.draftYaml
        ),
      }
    } catch {
      return null
    }
  })()
  if (!channels) return null
  const structuralCount =
    channels.structural.nodeKeys.length +
    (channels.structural.edgesChanged ? 1 : 0) +
    (channels.structural.otherChanged ? 1 : 0)
  if (channels.runtimeKeys.length === 0 && structuralCount === 0) return null

  return (
    <>
      {channels.runtimeKeys.length > 0 && (
        <div className={`${channelStyles.group} ${channelStyles.groupRuntime}`}>
          <div className={channelStyles.groupHead}>
            <span
              className={`${channelStyles.badge} ${channelStyles.badgeRuntime}`}
            >
              运行通道
            </span>
            运行配置
            <span className={channelStyles.groupCount}>
              （{channels.runtimeKeys.length} 项）
            </span>
          </div>
          {channels.runtimeKeys.map((key) => (
            <div key={key} className={channelStyles.diffRow}>
              <span className={channelStyles.diffField}>
                {formatRuntimeKey(key)}
              </span>
              <span>→</span>
              <span className={channelStyles.diffValue}>
                {runtimeValueAt(channels.proposal, key)}
              </span>
            </div>
          ))}
          <div className={channelStyles.groupHint}>
            应用后立即对新执行生效，不产生新版本；进行中的任务不受影响。
          </div>
          <div className={channelStyles.groupActions}>
            <button
              type="button"
              className={channelStyles.applyRuntimeButton}
              disabled={studio?.runtimeSaving}
              onClick={() => void studio?.saveRuntimeConfig(props.draftYaml)}
            >
              应用并生效
            </button>
          </div>
        </div>
      )}
      {structuralCount > 0 && (
        <div className={channelStyles.group}>
          <div className={channelStyles.groupHead}>
            <span
              className={`${channelStyles.badge} ${channelStyles.badgeStructure}`}
            >
              结构通道
            </span>
            结构变更
            <span className={channelStyles.groupCount}>
              （{structuralCount} 项）
            </span>
          </div>
          {channels.structural.nodeKeys.map((key) => (
            <div key={key} className={channelStyles.diffRow}>
              <span className={channelStyles.diffField}>{key}</span>
            </div>
          ))}
          {channels.structural.edgesChanged && (
            <div className={channelStyles.diffRow}>
              <span className={channelStyles.diffField}>连线（edges）</span>
            </div>
          )}
          {channels.structural.otherChanged && (
            <div className={channelStyles.diffRow}>
              <span className={channelStyles.diffField}>
                顶层声明（label / intake 等）
              </span>
            </div>
          )}
          <div className={channelStyles.groupHint}>
            存为草稿后随顶栏「发布」生成新版本生效；不影响当前生效的运行配置。
          </div>
          <div className={channelStyles.groupActions}>
            <button
              type="button"
              className={styles.draftButton}
              onClick={() =>
                studio &&
                props.onApply(
                  combineYamlStructureRuntime(props.draftYaml, studio.draftYaml)
                )
              }
            >
              存为草稿
            </button>
          </div>
        </div>
      )}
    </>
  )
}
