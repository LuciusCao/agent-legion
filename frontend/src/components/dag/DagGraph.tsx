import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Background,
  Controls,
  MiniMap,
  Node,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import * as dagre from 'dagre'
import { buildRfEdges, DagEdgeLabels } from './DagEdgeLabels'
import { DagEdge as DagEdgeComponent } from './DagEdge'
import { DagNode as DagNodeComponent } from './DagNode'
import type { DagNodeData } from './DagNode'
import type { DagNodeChangeType } from './dagNodeTypes'
import type { DagNodeStatus } from '../dagNodeStatus'
import type { ExecutorKind } from '../../types/jobTypes'
import { NodeDetailsPanel } from '../NodeDetailsPanel'
import { filterRelevantRuns } from '../../lib/jobRuns'
import { estimateDagNodeHeight } from '../dagNodeHeight'
import { applyHighlight } from '../dagHighlight'
import styles from './DagGraph.module.css'


export interface DagGraphNode {
  key: string
  label: string
  status: DagNodeStatus
  created_at: string
  duration?: number
  executorKind?: ExecutorKind | null
  executorId?: string | null
  agentId?: string | null
  workerId?: string | null
  capability?: string
  executorUnbound?: boolean
  topologyBadges?: Array<'start' | 'entry' | 'branch' | 'terminal'>
  terminalOutcome?: string
  inputs?: string[]
  outputs?: string[]
  changeType?: DagNodeChangeType
  ghost?: boolean
}

export interface DagGraphEdge {
  from: string
  to: string
  label?: string
  conditional?: boolean
  ghost?: boolean
}

export type { DagNodeChangeType }
export type DagNode = DagGraphNode
export type DagEdge = DagGraphEdge
export interface NodeRunSummary {
  id: number
  node_key: string
  status: string
  started_at: string
  exit_code?: number | null
  error_message?: string | null
  runner?: string
}

interface DagGraphProps {
  nodes: DagGraphNode[]
  edges: DagGraphEdge[]
  runs?: NodeRunSummary[]
  onViewLogs?: (nodeKey: string) => void
  selectedNode?: string | null
  onSelectedNodeChange?: (nodeKey: string | null) => void
  hideNodeDetails?: boolean
}

const NODE_WIDTH = 240
const FIT_VIEW_OPTIONS = { padding: 0.18, minZoom: 0.35, maxZoom: 1.2 }
const nodeTypes = { dagNode: DagNodeComponent }
const edgeTypes = { dagEdge: DagEdgeComponent }

type NormalizedExecutorKind = NonNullable<DagNodeData['executorKind']>

function normalizeExecutorKind(
  kind?: ExecutorKind | null
): DagNodeData['executorKind'] {
  if (!kind) return null
  return kind as NormalizedExecutorKind
}

function computeLayout(nodes: DagGraphNode[], edges: DagGraphEdge[]) {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 100 })
  g.setDefaultEdgeLabel(() => ({}))

  const heightMap = new Map<string, number>()
  for (const node of nodes) {
    const height = estimateDagNodeHeight(node)
    heightMap.set(node.key, height)
    g.setNode(node.key, { width: NODE_WIDTH, height })
  }
  for (const edge of edges) {
    g.setEdge(edge.from, edge.to)
  }

  dagre.layout(g)

  const rfNodes: Node<DagNodeData>[] = nodes.map((node) => {
    const gNode = g.node(node.key)
    const height = heightMap.get(node.key)!
    return {
      id: node.key,
      type: 'dagNode',
      position: { x: gNode.x - NODE_WIDTH / 2, y: gNode.y - height / 2 },
      data: {
        label: node.label,
        status: node.status,
        duration: node.duration,
        executorKind: normalizeExecutorKind(node.executorKind),
        executorId: node.executorId ?? null,
        agentId: node.agentId ?? null,
        workerId: node.workerId ?? null,
        nodeKey: node.capability ? node.key : undefined,
        capability: node.capability,
        executorUnbound: node.executorUnbound ?? false,
        topologyBadges: node.topologyBadges,
        terminalOutcome: node.terminalOutcome,
        inputs: node.inputs || [],
        outputs: node.outputs || [],
        changeType: node.changeType,
        ghost: node.ghost ?? false,
        // #276：高亮/置灰态放 data 而非 node.style/className，让 hover 时
        // 未受影响节点能保持 data 引用稳定（见下方 highlightMemo 的注释）。
        active: false,
        dimmed: false,
      },
    }
  })

  return { rfNodes, rfEdges: buildRfEdges(edges) }
}

export function DagGraph({
  nodes,
  edges,
  runs = [],
  onViewLogs = () => {},
  selectedNode: controlledSelectedNode,
  onSelectedNodeChange,
  hideNodeDetails = false,
}: DagGraphProps) {
  const { rfNodes: initialNodes, rfEdges: initialEdges } = useMemo(
    () => computeLayout(nodes, edges),
    [nodes, edges]
  )
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(initialNodes)
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(initialEdges)
  const [internalSelectedNode, setInternalSelectedNode] = useState<
    string | null
  >(null)
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)

  const isControlled = controlledSelectedNode !== undefined
  const selectedNode = isControlled
    ? controlledSelectedNode
    : internalSelectedNode
  const setSelectedNode = useCallback(
    (nodeKey: string | null) => {
      if (isControlled) {
        onSelectedNodeChange?.(nodeKey)
      } else {
        setInternalSelectedNode(nodeKey)
      }
    },
    [isControlled, onSelectedNodeChange]
  )

  useEffect(() => {
    setRfNodes(initialNodes)
    setRfEdges(initialEdges)
  }, [initialNodes, initialEdges, setRfNodes, setRfEdges])

  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node<DagNodeData>) => {
      setSelectedNode(node.id)
    },
    [setSelectedNode]
  )

  const onPaneClick = useCallback(() => {
    setSelectedNode(null)
  }, [setSelectedNode])

  const onNodeMouseEnter = useCallback(
    (_event: React.MouseEvent, node: Node<DagNodeData>) => {
      setHoveredNode(node.id)
    },
    []
  )

  const onNodeMouseLeave = useCallback(() => {
    setHoveredNode(null)
  }, [])

  // #276：hover/选中高亮经 applyHighlight（dagHighlight.ts，纯函数）计算——
  // 高亮态下沉为 node/edge 的 data 布尔字段，只替换实际翻转的条目，其余
  // 对象引用复用，hover 的渲染面从 O(全部节点+边) 收敛到 O(翻转条目)。
  // 完整的引用稳定性论证见 dagHighlight.ts 头注释与 dagNodeMemo.ts。
  const { highlightedEdges, highlightedNodes } = useMemo(
    () => applyHighlight(rfNodes, rfEdges, selectedNode || hoveredNode),
    [rfEdges, rfNodes, selectedNode, hoveredNode]
  )

  const relevantRuns = useMemo(
    () => filterRelevantRuns(runs, nodes),
    [runs, nodes]
  )

  const selectedData = useMemo(() => {
    if (!selectedNode) return null
    const node = rfNodes.find((n) => n.id === selectedNode)
    if (!node) return null
    const latestRun = relevantRuns
      .filter((run) => run.node_key === selectedNode)
      .sort(
        (a, b) =>
          new Date(b.started_at).getTime() - new Date(a.started_at).getTime()
      )[0]
    return {
      nodeKey: selectedNode,
      data: node.data,
      latestRun: latestRun
        ? {
            ...latestRun,
            error_message: latestRun.error_message ?? '',
            runner: latestRun.runner ?? '',
          }
        : null,
    }
  }, [rfNodes, relevantRuns, selectedNode])

  return (
    <div className={styles.graphContainer}>
      <div
        className={styles.flowWrapper}
        data-testid="dag-flow-wrapper"
        data-fit-view-padding={FIT_VIEW_OPTIONS.padding}
      >
        <ReactFlow
          nodes={highlightedNodes}
          edges={highlightedEdges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          onNodeMouseEnter={onNodeMouseEnter}
          onNodeMouseLeave={onNodeMouseLeave}
          fitView
          fitViewOptions={FIT_VIEW_OPTIONS}
          attributionPosition="bottom-left"
        >
          <Background gap={16} />
          <Controls />
          <MiniMap
            nodeStrokeWidth={3}
            zoomable
            pannable
            className={styles.miniMap}
          />
        </ReactFlow>
      </div>
      <DagEdgeLabels edges={edges} />
      {selectedData && !hideNodeDetails && (
        <NodeDetailsPanel
          nodeKey={selectedData.nodeKey}
          data={selectedData.data}
          latestRun={selectedData.latestRun}
          onViewLogs={onViewLogs}
        />
      )}
    </div>
  )
}
