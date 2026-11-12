import { formatRuntimeChangedKeys } from '../shared/workflowRuntimeSlice'
import styles from './WorkflowPublishReviewDialog.module.css'

/**
 * M48 发布复核框的 runtime 信息条（自 WorkflowPublishReviewDialogChanges
 * 拆出，文件预算）：runtime 字段不随复核框 diff 呈现，其去向在此明示——
 * 已经运行通道单独保存生效（不随本次发布）/ 尚未保存（将随发布写入
 * 新版本，可先在「运行配置」区单独保存立即生效）。
 * M49：createsRevision=false（runtime-only 确认，如 agent 发布请求的
 * 运行配置保存）时没有「新版本」可写入——文案改为「确认后立即生效 +
 * 追加配置版本」，与标题「保存节点运行配置」同一口径。
 */
export function WorkflowPublishReviewDialogRuntimeNote(props: {
  createsRevision?: boolean
  runtimeChangedKeys: string[]
  runtimeSavedKeys: string[]
}) {
  const createsRevision = props.createsRevision ?? true
  if (props.runtimeChangedKeys.length > 0) {
    if (!createsRevision) {
      return (
        <p className={styles.runtimePending} role="note">
          运行配置修改（{formatRuntimeChangedKeys(props.runtimeChangedKeys)}
          ）：确认后立即对新执行生效并追加一条配置版本，不创建新
          revision；进行中的任务不受影响。
        </p>
      )
    }
    return (
      <p className={styles.runtimePending} role="note">
        运行配置修改（{formatRuntimeChangedKeys(props.runtimeChangedKeys)}
        ）尚未通过运行通道保存——将随本次发布一并写入新版本；如需立即生效，可在「运行配置」区单独保存。
      </p>
    )
  }
  if (props.runtimeSavedKeys.length > 0) {
    return (
      <p className={styles.runtimeExcluded} role="note">
        运行配置变更（{formatRuntimeChangedKeys(props.runtimeSavedKeys)}
        ）已通过运行通道单独保存并生效，不包含在本次发布中，也不会出现在上方
        diff。
      </p>
    )
  }
  return null
}
