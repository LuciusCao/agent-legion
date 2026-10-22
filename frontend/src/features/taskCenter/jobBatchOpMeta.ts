import type { JobBatchOperationKind } from '../../api/jobBatchOperationsApi'

/**
 * 批量操作（#946）kind 的呈现元数据：卡片标题/副标/成功计数标签/图标
 * （Material Symbols 命名，视觉纪律：禁 emoji）与受理通知的动作措辞。
 * 单一来源——TaskCard 适配器与 TaskAcceptToast 共用。
 */
export type JobBatchOpMeta = {
  title: string
  subtitle: string
  /** 成功计数的动作标签（如「重跑 1,024」）。 */
  okLabel: string
  icon: string
  /** 受理通知措辞：「正在后台${action}」。 */
  action: string
}

export const JOB_BATCH_OP_META: Record<JobBatchOperationKind, JobBatchOpMeta> =
  {
    rerun: {
      title: '批量重跑',
      subtitle: '按节点重跑',
      okLabel: '重跑',
      icon: 'restart_alt',
      action: '重跑',
    },
    rerun_by_failure: {
      title: '批量重跑',
      subtitle: '按失败类别',
      okLabel: '重跑',
      icon: 'sync',
      action: '按失败类别重跑',
    },
    delete: {
      title: '批量删除',
      subtitle: '移除任务',
      okLabel: '删除',
      icon: 'delete',
      action: '删除',
    },
    run_to: {
      title: '批量运行',
      subtitle: '运行到节点',
      okLabel: '触发',
      icon: 'skip_next',
      action: '运行到目标节点',
    },
    pause: {
      title: '批量暂停',
      subtitle: '暂停执行',
      okLabel: '暂停',
      icon: 'pause',
      action: '暂停',
    },
    resume: {
      title: '批量恢复',
      subtitle: '恢复执行',
      okLabel: '恢复',
      icon: 'play_arrow',
      action: '恢复',
    },
    upgrade_workflow: {
      title: '批量升级',
      subtitle: 'workflow 版本',
      okLabel: '升级',
      icon: 'arrow_circle_up',
      action: '升级 workflow',
    },
    package: {
      title: '批量打包',
      subtitle: '导出 zip',
      okLabel: '打包',
      icon: 'archive',
      action: '打包',
    },
    clear_packed: {
      title: '清除打包标记',
      subtitle: '批量清除',
      okLabel: '清除',
      icon: 'unarchive',
      action: '清除打包标记',
    },
  }

/** 逐 job 结果 reason_code 的中文标签；未收录的码原样展示（内部码可读）。 */
export const JOB_BATCH_OP_REASON_LABEL: Record<string, string> = {
  not_found: '任务不存在',
  not_completed: '任务未完成',
  busy: '正在执行中',
  terminal: '终态不可操作',
  already_paused: '已处于暂停',
  not_paused: '未处于暂停',
  state_changed: '状态已变化',
  stale: '状态已过期',
  node_not_found: '节点不存在',
  node_not_executable: '入口节点不可执行',
  upstream_failed: '上游节点失败',
  upgrade_failed: '升级失败',
  already_current: '已是最新版本',
  protection_unprovable: '无法验证升级保护条件',
  invalid_node_config: '节点配置无效',
  cancelled: '已取消',
  awaiting_approval: '等待审批',
  cleanup_failed: '清理失败',
  internal_error: '内部错误',
}

export function jobBatchOpReasonLabel(code: string): string {
  return JOB_BATCH_OP_REASON_LABEL[code] ?? code
}
