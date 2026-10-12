import { fetchAndUpsert, type RowRefetchDeps } from './studioChatCalibration'

/** missed 帧（无 seq 指向未知 id 的原地更新帧，#1236）的点读失败恢复
 *（#1289）：目标行在校准窗口外时增量补齐（after_seq 从当前最大 seq 读）
 * 永远取不回它——瞬时失败若无重试，该行永久停在旧态。本模块在点读失败后
 * 对目标行安排短延迟有界重试；点读落地复用 fetchAndUpsert 的代次守卫与
 * 新鲜度守卫（#1288）。单向依赖 calibration，不得反向 import。 */

// 重试界与间隔：用尽即停（防持续故障空转，同 #1270 纪律），之后等下次
// 校准/事件的自然触发。
const MISSED_REFETCH_RETRY_MS = 2_000
const MISSED_REFETCH_MAX_RETRIES = 2

/** 点读失败后的有界重试（#1289）：同 id 同代次只挂一条链
 *（missedRetryRef 去重——重复 missed 帧不倍增重试）；代次推进后旧链点火
 * 即被代次守卫拦下，旧条目不再占用（A→B→A 回来可重新挂链）。 */
function scheduleMissedRowRetry(
  deps: RowRefetchDeps,
  messageId: string,
  attempt: number
): void {
  if (attempt > MISSED_REFETCH_MAX_RETRIES) return
  const generation = deps.sessionGenerationRef.current
  const pending = deps.missedRetryRef.current
  if (pending.get(messageId) === generation) return
  pending.set(messageId, generation)
  setTimeout(() => {
    // 只清自己挂的条目：代次推进后同 id 的新链不得被旧定时器摘牌。
    if (pending.get(messageId) === generation) pending.delete(messageId)
    if (deps.sessionGenerationRef.current !== generation) return
    void fetchAndUpsert(deps, messageId, generation).then(undefined, () =>
      scheduleMissedRowRetry(deps, messageId, attempt + 1)
    )
  }, MISSED_REFETCH_RETRY_MS)
}

/** missed 帧（#1236）：先按 id 点读——该行在校准窗口外时增量补齐永远取
 * 不回它。点读成功后该行变为已知，后续更新帧照常 upsert；点读失败
 *（404/网络）对目标行安排有界重试（#1289），并回退到旧的增量补齐路径。
 * 无 terminal 连锁：terminal 行必带 seq 以新行到达（upsert 即知），
 * 不经过 missed 路径。 */
export function handleMissedMessageFrame(
  deps: RowRefetchDeps,
  messageId: string
): void {
  void fetchAndUpsert(deps, messageId).then(undefined, () => {
    scheduleMissedRowRetry(deps, messageId, 1)
    void deps
      .refillMessages()
      .then((hasTerminal) => {
        if (hasTerminal) {
          void deps.refillMessages(0).catch(() => undefined)
          deps.onTerminal()
        }
      })
      .catch(() => undefined)
  })
}
