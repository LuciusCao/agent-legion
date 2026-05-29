import { parseTimeSeconds } from './formatters'
import type { InteractionNode } from '../types'

export interface IndexedTrigger {
  index: number
  trigger: number
}

/**
 * 预处理 interactions，提取有效的 trigger_time。
 * 假设输入已按 trigger_time 升序排列（后端 pipeline 保证）。
 */
export function prepareIndexedTriggers(
  interactions: InteractionNode[]
): IndexedTrigger[] {
  return interactions
    .map((node, index) => ({
      index,
      trigger: parseTimeSeconds(node.trigger_time),
    }))
    .filter((item) => Number.isFinite(item.trigger))
}

/**
 * 二分查找最后一个 trigger <= time 的 interaction 的原始 index。
 * @param time - 当前时间（秒）
 * @param indexedTriggers - 必须是按 trigger 升序排列的有效数组（由 prepareIndexedTriggers 生成）
 * @returns 原始数组中的 index，如果没有匹配则返回 -1
 */
export function binarySearchTriggerIndex(
  time: number,
  indexedTriggers: IndexedTrigger[]
): number {
  let left = 0
  let right = indexedTriggers.length - 1
  let result = -1

  while (left <= right) {
    const mid = Math.floor((left + right) / 2)
    if (indexedTriggers[mid].trigger <= time) {
      result = indexedTriggers[mid].index
      left = mid + 1
    } else {
      right = mid - 1
    }
  }

  return result
}
