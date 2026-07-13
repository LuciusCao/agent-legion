import type { DagGraphNode } from './DagGraph'

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
