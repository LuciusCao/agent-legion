import { parseTimeSeconds } from './formatters'
import type { InteractionNode } from '../types'

export function binarySearchTriggerIndex(
  time: number,
  interactions: InteractionNode[]
): number {
  const valid = interactions
    .map((item, index) => ({
      index,
      triggerTime: parseTimeSeconds(item.trigger_time),
    }))
    .filter((item) => Number.isFinite(item.triggerTime))

  let left = 0
  let right = valid.length - 1
  let result = -1

  while (left <= right) {
    const mid = Math.floor((left + right) / 2)
    if (valid[mid].triggerTime <= time) {
      result = valid[mid].index
      left = mid + 1
    } else {
      right = mid - 1
    }
  }

  return result
}
