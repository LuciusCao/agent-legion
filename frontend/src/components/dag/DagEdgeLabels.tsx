import { MarkerType, type Edge } from '@xyflow/react'
import type { DagGraphEdge } from './DagGraph'
import type { DagEdgeData } from './DagEdge'

export type RfDagEdge = Edge<DagEdgeData>

// #276：边走自定义 dagEdge 类型（渲染逻辑见 DagEdge.tsx），高亮态放
// data.highlighted；这里的 data 是常态初始值（highlighted 缺省 = 从未进入
// 高亮模式，DagEdge 原样透传本 style），DagGraph 的高亮 useMemo 只在
// highlighted 翻转时新建 data 对象，其余边引用稳定。
// #668：常态描边加深加粗（#6b7280 / 2.5），与 dagHighlight 退出高亮时
// 还原的 marker 颜色保持一致；置灰态（DagEdge 的 STROKE_DEFAULT +
// 0.4 透明度）维持明显浅于常态。
export function buildRfEdges(edges: DagGraphEdge[]): RfDagEdge[] {
  return edges.map((edge, idx) => ({
    id: `e-${edge.from}-${edge.to}-${idx}`,
    source: edge.from,
    target: edge.to,
    type: 'dagEdge',
    label: edge.label || undefined,
    data: {},
    markerEnd: { type: MarkerType.ArrowClosed, color: '#6b7280' },
    style: {
      stroke: '#6b7280',
      strokeWidth: 2.5,
      strokeDasharray: edge.conditional
        ? '6 4'
        : edge.ghost
          ? '3 3'
          : undefined,
      opacity: edge.ghost ? 0.5 : undefined,
    },
    labelStyle: { fill: '#374151', fontSize: 12, fontWeight: 600 },
    labelBgStyle: { fill: '#ffffff', fillOpacity: 0.9 },
  }))
}

export function DagEdgeLabels({ edges }: { edges: DagGraphEdge[] }) {
  const labeledEdges = edges.filter(
    (edge): edge is DagGraphEdge & { label: string } => Boolean(edge.label)
  )
  if (labeledEdges.length === 0) return null
  return (
    <ul
      aria-label="Edge labels"
      style={{
        position: 'absolute',
        width: 1,
        height: 1,
        padding: 0,
        margin: -1,
        overflow: 'hidden',
        clip: 'rect(0, 0, 0, 0)',
        whiteSpace: 'nowrap',
        border: 0,
      }}
    >
      {labeledEdges.map((edge, idx) => (
        <li key={`${edge.from}-${edge.to}-${idx}`}>{edge.label}</li>
      ))}
    </ul>
  )
}
