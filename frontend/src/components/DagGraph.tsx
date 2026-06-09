import { useId } from 'react'
import styles from './DagGraph.module.css'

export interface DagNode {
  key: string
  label: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  x: number
  y: number
  duration?: number
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

const NODE_WIDTH = 100
const NODE_HEIGHT = 50
const PADDING = 24

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
  completed: '✅',
  running: '⏳',
  failed: '❌',
  pending: '',
}

export function DagGraph({
  nodes,
  edges,
  selectedNodeKey,
  onNodeClick,
}: DagGraphProps) {
  const arrowMarkerId = useId().replace(/:/g, '-')

  if (nodes.length === 0) {
    return (
      <svg
        className={styles.dagGraph}
        viewBox="0 0 200 100"
        preserveAspectRatio="xMidYMid meet"
      />
    )
  }

  const minX = Math.min(...nodes.map((n) => n.x - NODE_WIDTH / 2)) - PADDING
  const minY = Math.min(...nodes.map((n) => n.y - NODE_HEIGHT / 2)) - PADDING
  const maxX = Math.max(...nodes.map((n) => n.x + NODE_WIDTH / 2)) + PADDING
  const maxY = Math.max(...nodes.map((n) => n.y + NODE_HEIGHT / 2)) + PADDING
  const viewWidth = Math.max(1, maxX - minX)
  const viewHeight = Math.max(1, maxY - minY)

  const nodeMap = new Map<string, DagNode>()
  for (const node of nodes) {
    nodeMap.set(node.key, node)
  }

  return (
    <svg
      className={styles.dagGraph}
      viewBox={`${minX} ${minY} ${viewWidth} ${viewHeight}`}
      preserveAspectRatio="xMidYMid meet"
      width="100%"
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
        {edges.map((edge, idx) => {
          const from = nodeMap.get(edge.from)
          const to = nodeMap.get(edge.to)
          if (!from || !to) return null
          const isCompleted = from.status === 'completed'
          return (
            <line
              key={`${edge.from}-${edge.to}-${idx}`}
              x1={from.x + NODE_WIDTH / 2}
              y1={from.y}
              x2={to.x - NODE_WIDTH / 2}
              y2={to.y}
              className={[
                styles.edgeLine,
                isCompleted ? styles.edgeLineCompleted : styles.edgeLinePending,
              ].join(' ')}
              markerEnd={`url(#${arrowMarkerId})`}
            />
          )
        })}
      </g>

      <g data-testid="nodes">
        {nodes.map((node) => {
          const isSelected = selectedNodeKey === node.key
          const icon = STATUS_ICON[node.status]
          return (
            <g
              key={node.key}
              data-node={node.key}
              onClick={() => onNodeClick?.(node.key)}
            >
              <rect
                x={node.x - NODE_WIDTH / 2}
                y={node.y - NODE_HEIGHT / 2}
                width={NODE_WIDTH}
                height={NODE_HEIGHT}
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
                <text x={node.x} y={node.y - 10} className={styles.nodeIcon}>
                  {icon}
                </text>
              )}
              <text
                x={node.x}
                y={icon ? node.y + 6 : node.y}
                className={styles.nodeLabel}
              >
                {node.label}
              </text>
              {typeof node.duration === 'number' && (
                <text
                  x={node.x}
                  y={node.y + 18}
                  className={styles.nodeDuration}
                >
                  {node.duration}s
                </text>
              )}
            </g>
          )
        })}
      </g>
    </svg>
  )
}
