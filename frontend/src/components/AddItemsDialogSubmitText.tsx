import { BULK_RUN_SUBMIT_THRESHOLD } from '../api/materialsApi'
import { isRequestTimeoutError } from '../api/requestTimeout'
import styles from './AddItemsDialog.module.css'

/**
 * 添加条目对话框的提交反馈文案与大批量提示（#1157 phase0 过渡兜底；
 * 从 AddItemsDialog 拆出，体积预算同各 AddItems 面板姊妹模块的先例）。
 * 完整解（Studio 接异步提交）随 #1117 进 0.8.2。
 */

/** 全量重复 400 的对外机器契约字符串（docs/workspace-api-tokens.md 教
 * 外部调用方精确相等判断「已存在」，契约测试钉死，不得改动）；前端据此
 * 把该 400 的 toast 可读化（tower 裁决 A：机器契约不动）。 */
const ALL_ITEMS_EXIST_DETAIL = 'No tasks were resolved from input'

/** 成功 toast：响应携带跳过计数（已存在 dedup 过滤 + 写入竞态被并发
 * run 认领），有跳过时展示「新建 N 跳过 M」，不再只报新建数。 */
export function submitOkText(createdCount: number, skippedCount: number) {
  return skippedCount > 0
    ? `运行已创建：新建 ${createdCount} 个任务，跳过 ${skippedCount} 个已存在条目`
    : `运行已创建，共 ${createdCount} 个任务`
}

/** 提交失败的展示文案。超时/网络中断（fetch rejection 是 TypeError）时
 * 服务端可能仍在建行——客户端中止不撤销服务端动作，文案不引导盲重试
 * （确定性 run id + dedup 使迟到确认与安全重提都有兜底）；全量重复 400
 * 可读化为「均已存在、0 个新建」（计数取本地提交数——400 语义即本次
 * 0 新建，无需改动机器契约）。 */
export function submitFailText(error: unknown, submittedCount: number) {
  if (isRequestTimeoutError(error) || error instanceof TypeError) {
    return '提交结果未返回：服务端可能仍在处理，请稍后刷新任务列表确认；确定性运行 ID 与条目去重会防止重复建任务，无需立即重新提交'
  }
  if (
    error instanceof Error &&
    (error as { status?: unknown }).status === 400 &&
    error.message === ALL_ITEMS_EXIST_DETAIL
  ) {
    return `全部 ${submittedCount} 个条目均已存在对应任务，0 个新建`
  }
  const message = error instanceof Error ? error.message : '创建运行失败'
  return `创建运行失败: ${message}`
}

/** 大批量（>1000 条）提交的整体受理提示；阈值与 createRun 的写超时
 * 豁免共用（materialsApi.BULK_RUN_SUBMIT_THRESHOLD）。 */
export function AddItemsDialogBulkHint({ totalItems }: { totalItems: number }) {
  if (totalItems <= BULK_RUN_SUBMIT_THRESHOLD) return null
  return (
    <div className={styles.bulkHint} data-testid="bulk-submit-hint">
      大批量提交将整体受理：服务端同步处理 {totalItems}{' '}
      个条目可能耗时较久，提交后请耐心等待并稍后刷新任务列表确认结果；如希望更快看到部分进度，建议分批（每批不超过{' '}
      {BULK_RUN_SUBMIT_THRESHOLD} 条）提交。
    </div>
  )
}
