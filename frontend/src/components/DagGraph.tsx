import { useId, useMemo, useState } from 'react'
import styles from './DagGraph.module.css'

export interface DagNode {
  key: string
  label: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  duration?: number
  inputs?: string[]
  outputs?: string[]
}

export interface DagEdge {
  from: string
  to: string
}

interface DagGraphProps {
  nodes: DagNode[]
  edges: DagEdge[]
  selectedNodeKey?: string | null
  onNodeClick?: (nodeKey: string) => void
}

const NODE_PADDING_X = 16
const CHIP_HEIGHT = 14
const CHIP_GAP = 4
const BASE_WIDTH = 100
const BASE_HEIGHT = 50

const STATUS_FILL: Record<DagNode['status'], string> = {
  completed: '#dcfce7',
  running: '#dbeafe',
  failed: '#fee2e2',
  pending: '#f5f5f5',
}

const STATUS_STROKE: Record<DagNode['status'], string> = {
  completed: '#15803d',
  running: '#1d4ed8',
  failed: '#b91c1c',
  pending: '#999',
}

const STATUS_ICON: Record<DagNode['status'], string> = {
  completed: 'check_circle',
  running: 'hourglass_empty',
  failed: 'error',
  pending: '',
}

function computeNodeSize(node: DagNode): { width: number; height: number } {
  const labelWidth = node.label.length * 12 + 24
  const ioCount = (node.inputs?.length || 0) + (node.outputs?.length || 0)
  const chipRows = ioCount > 0 ? Math.ceil(Math.min(ioCount, 3) / 2) : 0
  const chipHeight =
    chipRows > 0 ? chipRows * (CHIP_HEIGHT + CHIP_GAP) + CHIP_GAP : 0

  const visibleChipCount = Math.min(ioCount, 2)
  const hiddenCount = ioCount - visibleChipCount
  const chipWidth = visibleChipCount * 52 + (hiddenCount > 0 ? 20 : 0) + 16

  const width = Math.max(BASE_WIDTH, labelWidth + NODE_PADDING_X * 2, chipWidth)
  const height = BASE_HEIGHT + chipHeight
  return { width, height }
}

function computeLayout(nodes: DagNode[], edges: DagEdge[]) {
  // 1. Compute topological depth (rank) for each node
  const depthMap = new Map<string, number>()
  for (const node of nodes) {
    depthMap.set(node.key, 0)
  }
  let changed = true
  while (changed) {
    changed = false
    for (const edge of edges) {
      const fromDepth = depthMap.get(edge.from)!
      const toDepth = depthMap.get(edge.to)!
      if (toDepth < fromDepth + 1) {
        depthMap.set(edge.to, fromDepth + 1)
        changed = true
      }
    }
  }

  // 2. Group nodes by depth, preserving original YAML order
  const groups = new Map<number, DagNode[]>()
  for (const node of nodes) {
    const depth = depthMap.get(node.key)!
    if (!groups.has(depth)) groups.set(depth, [])
    groups.get(depth)!.push(node)
  }

  // 3. Compute sizes
  const sizeMap = new Map<string, { width: number; height: number }>()
  for (const node of nodes) {
    sizeMap.set(node.key, computeNodeSize(node))
  }

  const RANKSEP = 100
  const NODESEP = 60
  const MARGIN = 24

  // 4. Position nodes: depth → x, index in group → y
  type PositionedNode = DagNode & {
    x: number
    y: number
    width: number
    height: number
  }
  const positioned: PositionedNode[] = []
  let currentX = MARGIN

  const depths = Array.from(groups.keys()).sort((a, b) => a - b)
  for (const depth of depths) {
    const group = groups.get(depth)!
    let maxWidthInGroup = 0
    for (const node of group) {
      const size = sizeMap.get(node.key)!
      maxWidthInGroup = Math.max(maxWidthInGroup, size.width)
    }

    let currentY = MARGIN
    for (const node of group) {
      const size = sizeMap.get(node.key)!
      positioned.push({
        ...node,
        x: currentX + size.width / 2,
        y: currentY + size.height / 2,
        width: size.width,
        height: size.height,
      })
      currentY += size.height + NODESEP
    }

    currentX += maxWidthInGroup + RANKSEP
  }

  // 5. Compute edge paths (simple orthogonal polyline)
  const nodeMap = new Map<string, PositionedNode>()
  for (const node of positioned) {
    nodeMap.set(node.key, node)
  }

  const positionedEdges = edges.map((edge) => {
    const from = nodeMap.get(edge.from)
    const to = nodeMap.get(edge.to)
    if (!from || !to) {
      return { ...edge, points: [] as Array<{ x: number; y: number }> }
    }
    const startX = from.x + from.width / 2
    const startY = from.y
    const endX = to.x - to.width / 2
    const endY = to.y
    const midX = (startX + endX) / 2

    return {
      ...edge,
      points: [
        { x: startX, y: startY },
        { x: midX, y: startY },
        { x: midX, y: endY },
        { x: endX, y: endY },
      ],
    }
  })

  return { nodes: positioned, edges: positionedEdges }
}

function buildPath(points: Array<{ x: number; y: number }>): string {
  if (points.length < 2) return ''
  let d = `M ${points[0].x} ${points[0].y}`
  for (let i = 1; i < points.length; i++) {
    d += ` L ${points[i].x} ${points[i].y}`
  }
  return d
}

export function DagGraph({
  nodes,
  edges,
  selectedNodeKey,
  onNodeClick,
}: DagGraphProps) {
  const arrowMarkerId = useId().replace(/:/g, '-')
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)

  const { nodes: layoutNodes, edges: layoutEdges } = useMemo(
    () => computeLayout(nodes, edges),
    [nodes, edges]
  )

  if (nodes.length === 0) {
    return (
      <svg
        className={styles.dagGraph}
        viewBox="0 0 200 100"
        preserveAspectRatio="xMidYMid meet"
      />
    )
  }

  const minX = Math.min(...layoutNodes.map((n) => n.x - n.width / 2)) - 24
  const minY = Math.min(...layoutNodes.map((n) => n.y - n.height / 2)) - 24
  const maxX = Math.max(...layoutNodes.map((n) => n.x + n.width / 2)) + 24
  const maxY = Math.max(...layoutNodes.map((n) => n.y + n.height / 2)) + 24
  const viewWidth = Math.max(1, maxX - minX)
  const viewHeight = Math.max(1, maxY - minY)

  const nodeMap = new Map<
    string,
    DagNode & { x: number; y: number; width: number; height: number }
  >()
  for (const node of layoutNodes) {
    nodeMap.set(node.key, node)
  }

  const upstream = new Set<string>()
  const downstream = new Set<string>()
  if (selectedNodeKey) {
    const queue = [selectedNodeKey]
    while (queue.length) {
      const cur = queue.shift()!
      for (const e of edges) {
        if (e.to === cur && !upstream.has(e.from)) {
          upstream.add(e.from)
          queue.push(e.from)
        }
      }
    }
    const dq = [selectedNodeKey]
    while (dq.length) {
      const cur = dq.shift()!
      for (const e of edges) {
        if (e.from === cur && !downstream.has(e.to)) {
          downstream.add(e.to)
          dq.push(e.to)
        }
      }
    }
  }

  const isDimmed = (key: string) => {
    if (!selectedNodeKey) return false
    if (key === selectedNodeKey) return false
    if (upstream.has(key) || downstream.has(key)) return false
    return true
  }

  return (
    <svg
      className={styles.dagGraph}
      viewBox={`${minX} ${minY} ${viewWidth} ${viewHeight}`}
      preserveAspectRatio="xMidYMid meet"
      width="100%"
      height="100%"
    >
      <defs>
        <marker
          id={arrowMarkerId}
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#999" />
        </marker>
      </defs>

      <g data-testid="edges">
        {layoutEdges.map((edge, idx) => {
          const from = nodeMap.get(edge.from)
          const to = nodeMap.get(edge.to)
          if (!from || !to) return null
          const isCompleted = from.status === 'completed'
          const isSelectedPath =
            selectedNodeKey &&
            (from.key === selectedNodeKey ||
              to.key === selectedNodeKey ||
              (upstream.has(from.key) && upstream.has(to.key)) ||
              (downstream.has(from.key) && downstream.has(to.key)))
          const dimmed = selectedNodeKey && !isSelectedPath
          return (
            <path
              key={`${edge.from}-${edge.to}-${idx}`}
              data-testid="edge"
              d={buildPath(edge.points)}
              className={[
                styles.edgeLine,
                isCompleted ? styles.edgeLineCompleted : styles.edgeLinePending,
              ].join(' ')}
              style={{ opacity: dimmed ? 0.2 : 1 }}
              markerEnd={`url(#${arrowMarkerId})`}
            />
          )
        })}
      </g>

      <g data-testid="nodes">
        {layoutNodes.map((node) => {
          const isSelected = selectedNodeKey === node.key
          const icon = STATUS_ICON[node.status]
          const dimmed = isDimmed(node.key)
          const ioCount =
            (node.inputs?.length || 0) + (node.outputs?.length || 0)
          const showChips = ioCount > 0
          const chipLimit = 2
          const allIo = [
            ...(node.inputs || []).map((i) => ({
              text: i,
              type: 'in' as const,
            })),
            ...(node.outputs || []).map((o) => ({
              text: o,
              type: 'out' as const,
            })),
          ]
          const visibleChips = allIo.slice(0, chipLimit)
          const hiddenCount = allIo.length - chipLimit

          return (
            <g
              key={node.key}
              data-node={node.key}
              role="button"
              tabIndex={0}
              aria-label={node.label}
              onClick={() => onNodeClick?.(node.key)}
              onMouseEnter={() => setHoveredNode(node.key)}
              onMouseLeave={() => setHoveredNode(null)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onNodeClick?.(node.key)
                }
              }}
              style={{ opacity: dimmed ? 0.3 : 1, transition: 'opacity 0.2s' }}
            >
              <rect
                x={node.x - node.width / 2}
                y={node.y - node.height / 2}
                width={node.width}
                height={node.height}
                rx={8}
                ry={8}
                fill={STATUS_FILL[node.status]}
                stroke={STATUS_STROKE[node.status]}
                strokeWidth={isSelected ? 3 : 1.5}
                className={[
                  styles.nodeRect,
                  isSelected ? styles.selectedShadow : '',
                ].join(' ')}
              />
              {icon && (
                <text
                  x={node.x - node.width / 2 + 16}
                  y={node.y - 4}
                  className={styles.nodeIcon}
                >
                  {icon}
                </text>
              )}
              <text
                x={node.x}
                y={icon ? node.y - 4 : node.y}
                className={styles.nodeLabel}
              >
                {node.label}
              </text>
              {typeof node.duration === 'number' && (
                <text
                  x={node.x}
                  y={node.y + 10}
                  className={styles.nodeDuration}
                >
                  {node.duration}s
                </text>
              )}
              {showChips && (
                <g
                  transform={`translate(${node.x - node.width / 2 + 8}, ${node.y + 14})`}
                >
                  {visibleChips.map((chip, cidx) => (
                    <g key={cidx} transform={`translate(${cidx * 52}, 0)`}>
                      <rect
                        width={48}
                        height={14}
                        rx={4}
                        ry={4}
                        fill={chip.type === 'in' ? '#e5e7eb' : '#dbeafe'}
                      />
                      <text x={24} y={10} className={styles.chipLabel}>
                        {chip.text.length > 8
                          ? chip.text.slice(0, 7) + '…'
                          : chip.text}
                      </text>
                    </g>
                  ))}
                  {hiddenCount > 0 && (
                    <g transform={`translate(${visibleChips.length * 52}, 0)`}>
                      <rect
                        width={20}
                        height={14}
                        rx={4}
                        ry={4}
                        fill="#f3f4f6"
                      />
                      <text x={10} y={10} className={styles.chipLabel}>
                        +{hiddenCount}
                      </text>
                    </g>
                  )}
                </g>
              )}
              {hoveredNode === node.key && (
                <g>
                  <rect
                    x={node.x - node.width / 2 + 2}
                    y={node.y - node.height / 2 + 2}
                    width={node.width - 4}
                    height={28}
                    rx={4}
                    ry={4}
                    fill="rgba(0,0,0,0.75)"
                  />
                  <text
                    x={node.x}
                    y={node.y - node.height / 2 + 16}
                    fill="white"
                    fontSize="8"
                    textAnchor="middle"
                  >
                    {(node.inputs || []).join(', ') || '—'} →{' '}
                    {(node.outputs || []).join(', ') || '—'}
                  </text>
                </g>
              )}
            </g>
          )
        })}
      </g>
    </svg>
  )
}
