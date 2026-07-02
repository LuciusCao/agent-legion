import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Background,
  Controls,
  Edge,
  MarkerType,
  MiniMap,
  Node,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import * as dagre from 'dagre'
import { buildRfEdges, DagEdgeLabels } from './DagEdgeLabels'
import { DagNode as DagNodeComponent } from './DagNode'
import type { DagNodeData } from './DagNode'
import type { DagNodeStatus } from './dagNodeStatus'
import { NodeDetailsPanel } from './NodeDetailsPanel'
import { filterRelevantRuns } from '../helpers'
import styles from './DagGraph.module.css'

export interface DagGraphNode {
  key: string
  label: string
  status: DagNodeStatus
  created_at: string
  duration?: number
  executorKind?: 'local' | 'pi' | 'openclaw' | null
  terminalOutcome?: string
  inputs?: string[]
  outputs?: string[]
}

export interface DagGraphEdge {
  from: string
  to: string
  label?: string
  conditional?: boolean
}

export type DagNode = DagGraphNode
export type DagEdge = DagGraphEdge

export interface NodeRunSummary {
  id: number
  node_key: string
  status: string
  started_at: string
  exit_code?: number | null
  error_message?: string | null
}

interface DagGraphProps {
  nodes: DagGraphNode[]
  edges: DagGraphEdge[]
  runs?: NodeRunSummary[]
  onViewLogs?: (nodeKey: string) => void
  selectedNode?: string | null
  onSelectedNodeChange?: (nodeKey: string | null) => void
}

const NODE_WIDTH = 240
const nodeTypes = { dagNode: DagNodeComponent }

const BASE_HEIGHT = 66
const SECTION_TITLE_HEIGHT = 14
const CHIP_ROW_HEIGHT = 17
const CHIPS_PER_ROW = 2

type NormalizedExecutorKind = NonNullable<DagNodeData['executorKind']>

function normalizeExecutorKind(
  kind?: 'local' | 'pi' | 'openclaw' | null
): DagNodeData['executorKind'] {
  if (!kind) return null
  return kind as NormalizedExecutorKind
}

function estimateNodeHeight(node: DagGraphNode): number {
  let height = BASE_HEIGHT
  const inputCount = node.inputs?.length || 0
  const outputCount = node.outputs?.length || 0
  if (inputCount > 0) {
    const inputRows = Math.ceil(Math.min(inputCount, 3) / CHIPS_PER_ROW)
    height += SECTION_TITLE_HEIGHT + inputRows * CHIP_ROW_HEIGHT
  }
  if (outputCount > 0) {
    const outputRows = Math.ceil(Math.min(outputCount, 3) / CHIPS_PER_ROW)
    height += SECTION_TITLE_HEIGHT + outputRows * CHIP_ROW_HEIGHT
  }
  return height
}

function computeLayout(nodes: DagGraphNode[], edges: DagGraphEdge[]) {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 100 })
  g.setDefaultEdgeLabel(() => ({}))

  const heightMap = new Map<string, number>()
  for (const node of nodes) {
    const height = estimateNodeHeight(node)
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
        terminalOutcome: node.terminalOutcome,
        inputs: node.inputs || [],
        outputs: node.outputs || [],
      },
    }
  })

  return { rfNodes, rfEdges: buildRfEdges(edges) }
}

function buildRelationMaps(rfEdges: Edge[]) {
  const edgeBySource: Record<string, Edge[]> = {}
  const edgeByTarget: Record<string, Edge[]> = {}
  for (const edge of rfEdges) {
    edgeBySource[edge.source] = edgeBySource[edge.source] || []
    edgeBySource[edge.source].push(edge)
    edgeByTarget[edge.target] = edgeByTarget[edge.target] || []
    edgeByTarget[edge.target].push(edge)
  }
  return { edgeBySource, edgeByTarget }
}

function collectAncestors(
  nodeId: string,
  edgeByTarget: Record<string, Edge[]>,
  ancestors: Set<string>
) {
  for (const edge of edgeByTarget[nodeId] || []) {
    if (!ancestors.has(edge.source)) {
      ancestors.add(edge.source)
      collectAncestors(edge.source, edgeByTarget, ancestors)
    }
  }
}

function collectDescendants(
  nodeId: string,
  edgeBySource: Record<string, Edge[]>,
  descendants: Set<string>
) {
  for (const edge of edgeBySource[nodeId] || []) {
    if (!descendants.has(edge.target)) {
      descendants.add(edge.target)
      collectDescendants(edge.target, edgeBySource, descendants)
    }
  }
}

export function DagGraph({
  nodes,
  edges,
  runs = [],
  onViewLogs = () => {},
  selectedNode: controlledSelectedNode,
  onSelectedNodeChange,
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

  const { highlightedEdges, highlightedNodes } = useMemo(() => {
    const activeNode = selectedNode || hoveredNode
    if (!activeNode) {
      return {
        highlightedEdges: rfEdges,
        highlightedNodes: rfNodes,
      }
    }

    const { edgeBySource, edgeByTarget } = buildRelationMaps(rfEdges)
    const ancestors = new Set<string>()
    const descendants = new Set<string>()
    collectAncestors(activeNode, edgeByTarget, ancestors)
    collectDescendants(activeNode, edgeBySource, descendants)

    const highlightedEdges = rfEdges.map((edge) => {
      const isHighlighted =
        edge.source === activeNode ||
        edge.target === activeNode ||
        (ancestors.has(edge.source) && edge.target === activeNode) ||
        (edge.source === activeNode && descendants.has(edge.target))
      return {
        ...edge,
        style: {
          ...edge.style,
          stroke: isHighlighted ? '#1d4ed8' : '#d1d5db',
          strokeWidth: isHighlighted ? 3 : 2,
          opacity: isHighlighted ? 1 : 0.4,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: isHighlighted ? '#1d4ed8' : '#d1d5db',
        },
      }
    })

    const highlightedNodes = rfNodes.map((node) => ({
      ...node,
      style: {
        ...node.style,
        opacity: node.id === activeNode ? 1 : 0.5,
      },
    }))

    return { highlightedEdges, highlightedNodes }
  }, [rfEdges, rfNodes, selectedNode, hoveredNode])

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
        ? { ...latestRun, error_message: latestRun.error_message ?? '' }
        : null,
    }
  }, [rfNodes, relevantRuns, selectedNode])

  return (
    <div className={styles.graphContainer}>
      <div className={styles.flowWrapper}>
        <ReactFlow
          nodes={highlightedNodes}
          edges={highlightedEdges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          onNodeMouseEnter={onNodeMouseEnter}
          onNodeMouseLeave={onNodeMouseLeave}
          fitView
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
      {selectedData && (
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
