import { MarkerType, type Edge } from '@xyflow/react'
import type { DagGraphEdge } from './DagGraph'

export function buildRfEdges(edges: DagGraphEdge[]): Edge[] {
  return edges.map((edge, idx) => ({
    id: `e-${edge.from}-${edge.to}-${idx}`,
    source: edge.from,
    target: edge.to,
    label: edge.label || undefined,
    markerEnd: { type: MarkerType.ArrowClosed, color: '#9ca3af' },
    style: {
      stroke: '#9ca3af',
      strokeWidth: 2,
      strokeDasharray: edge.conditional ? '6 4' : undefined,
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
