import { BULK_RUN_SUBMIT_THRESHOLD } from '../api/materialsApi'
import { isRequestTimeoutError } from '../api/requestTimeout'
import styles from './AddItemsDialog.module.css'

/**
 * 添加条目对话框的提交反馈文案与大批量提示（#1157 phase0 过渡兜底 + phase3
 * 异步接线；从 AddItemsDialog 拆出，体积预算同各 AddItems 面板姊妹模块的
 * 先例）。文案为用户视角定稿（评审验收 + 任务中心原型两轮确认后逐字确认）。
 */

/** 全量重复 400 的对外机器契约字符串（docs/workspace-api-tokens.md 教
 * 外部调用方精确相等判断「已存在」，契约测试钉死，不得改动）；前端据此
 * 把该 400 的 toast 可读化（tower 裁决 A：机器契约不动）。 */
const ALL_ITEMS_EXIST_DETAIL = 'No tasks were resolved from input'

/** 成功 toast：响应携带跳过计数（已存在 dedup 过滤 + 写入竞态被并发
 * run 认领），有跳过时一并告知跳过数，不再只报新建数。 */
export function submitOkText(createdCount: number, skippedCount: number) {
  return skippedCount > 0
    ? `已开始运行：新建 ${createdCount} 个任务；${skippedCount} 个条目已有任务，自动跳过。`
    : `运行已创建，共 ${createdCount} 个任务`
}

/** 提交失败的展示文案。超时/网络中断（fetch rejection 是 TypeError）时
 * 结果可能仍在路上——客户端中止不撤销服务端动作，文案引导先刷新确认、
 * 再决定重提（确定性 run id + dedup 保证重复提交不重复建任务）；全量
 * 重复 400 可读化为「都已存在」（计数取本地提交数——400 语义即本次
 * 0 新建，无需改动机器契约）。 */
export function submitFailText(error: unknown, submittedCount: number) {
  if (isRequestTimeoutError(error) || error instanceof TypeError) {
    return '网络较慢，暂时没收到结果。请稍后刷新任务列表查看；没出现的话再重新提交，已经提交的条目不会重复创建。'
  }
  if (
    error instanceof Error &&
    (error as { status?: unknown }).status === 400 &&
    error.message === ALL_ITEMS_EXIST_DETAIL
  ) {
    return `这 ${submittedCount} 个条目都已经有任务了，无需重复创建。`
  }
  const message = error instanceof Error ? error.message : '创建运行失败'
  return `创建运行失败: ${message}`
}

/** 大批量（>1000 条）提交的提示；阈值与异步受理切换共用
 * （materialsApi.BULK_RUN_SUBMIT_THRESHOLD）。phase3：超过阈值自动转
 * mode=async 后台创建（任务中心原型定稿文案）。 */
export function AddItemsDialogBulkHint({ totalItems }: { totalItems: number }) {
  if (totalItems <= BULK_RUN_SUBMIT_THRESHOLD) return null
  return (
    <div className={styles.bulkHint} data-testid="bulk-submit-hint">
      共 {totalItems.toLocaleString()} 个条目，数量较多，提交后将
      <b>转为后台创建</b>
      ：立即返回，不阻塞操作；进度集中在右上角「任务」里查看，期间可以提交其他批量操作。
    </div>
  )
}
