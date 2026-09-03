import type { DagGraphNode } from './dag/DagGraph'

/* #415：label 改两行 line-clamp 后，换行场景卡片实际高度比此估算多 ~16px
   （13px 字号 × 1.2 行高的第二行），由 DagGraph 的 nodesep 60 吸收（间隙
   收窄到 ~44px 但不重叠）。若调小 line-clamp 行数或 nodesep，需同步复核
   此常量。 */
const BASE_HEIGHT = 66
const DEFINITION_META_HEIGHT = 46
const TOPOLOGY_BADGES_HEIGHT = 25
const CHIP_GROUP_HEADER_HEIGHT = 20
const CHIP_ROW_HEIGHT = 25

export function estimateDagNodeHeight(node: DagGraphNode): number {
  let height = BASE_HEIGHT
  if (node.capability) height += DEFINITION_META_HEIGHT
  if (node.topologyBadges?.length) height += TOPOLOGY_BADGES_HEIGHT
  for (const count of [node.inputs?.length || 0, node.outputs?.length || 0]) {
    if (count > 0) {
      height += CHIP_GROUP_HEADER_HEIGHT + Math.min(count, 3) * CHIP_ROW_HEIGHT
    }
  }
  return height
}
