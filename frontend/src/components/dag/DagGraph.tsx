import React, {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useState,
} from 'react'
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
import { buildRfEdges, DagEdgeLabels } from './DagEdgeLabels'
import { computeLayout } from './dagLayout'
import { DagEdge as DagEdgeComponent } from './DagEdge'
import { DagNode as DagNodeComponent } from './DagNode'
import type { DagNodeData } from './DagNode'
import type { DagNodeChangeType } from './dagNodeTypes'
import type { DagNodeStatus } from '../dagNodeStatus'
import type { ExecutorKind } from '../../types/jobTypes'
import { NodeDetailsPanel } from '../NodeDetailsPanel'
import { filterRelevantRuns } from '../../lib/jobRuns'
import { applyHighlight, hoverReducer } from '../dagHighlight'
import type { TopologyBadge } from './dagNodeTypes'
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
  /** Studio 注入的 execution 缺口警告文案（#333）；见 dagNodeTypes。 */
  executionWarning?: string
  topologyBadges?: TopologyBadge[]
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

// 布局计算在 dagLayout.ts（#417 拆出：无边 dagre 退化布局治理 + 体积预算）。
function layoutGraph(nodes: DagGraphNode[], edges: DagGraphEdge[]) {
  return {
    ...computeLayout(nodes, edges, normalizeExecutorKind),
    rfEdges: buildRfEdges(edges),
  }
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
    () => layoutGraph(nodes, edges),
    [nodes, edges]
  )
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(initialNodes)
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(initialEdges)
  const [internalSelectedNode, setInternalSelectedNode] = useState<
    string | null
  >(null)
  // hover 链式对照（Codex review on #285）：翻转判断的基准是「上一次的
  // 视觉态」（上次 activeNode 的链路集合），而非未高亮基线——hover 从 A
  // 移到 B 时仍应置灰的节点才不会被全量重建。prevActiveNode 与 hoveredNode
  // 在同一个 reducer 里原子更新（一次 dispatch 一轮渲染；两个独立 useState
  // 各自调度会引入一轮多余的全量重算，render 计数测试能抓到）。
  const [hoverState, dispatchHover] = useReducer(hoverReducer, {
    hoveredNode: null,
    prevActiveNode: null,
  })
  const hoveredNode = hoverState.hoveredNode

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
      dispatchHover({ type: 'enter', id: node.id })
    },
    []
  )

  const onNodeMouseLeave = useCallback(() => {
    dispatchHover({ type: 'leave' })
  }, [])

  // #276：hover/选中高亮经 applyHighlight（dagHighlight.ts，纯函数）计算——
  // 高亮态下沉为 node/edge 的 data 布尔字段，只替换实际翻转的条目，其余
  // 对象引用复用，hover 的渲染面从 O(全部节点+边) 收敛到 O(翻转条目)。
  // 链式对照（Codex review on #285）：翻转判断的基准是「上一次的视觉态」
  // 而未高亮基线——hover 从 A 移到 B 时仍应置灰的节点才不会被全量重建。
  // 上次视觉态 = 上次 activeNode 在当前图形状上的高亮结果（纯函数重算，
  // 拖拽/布局变化时集合自动跟随）；prevActiveNode 在事件回调里与 hovered
  // 同步更新（同一次批处理，单轮渲染——经 effect 链式更新会引入一轮以旧
  // 基准的全量重建，render 计数测试能抓到）。
  const prevActiveNode = hoverState.prevActiveNode
  const activeNode = selectedNode || hoveredNode
  const { highlightedEdges, highlightedNodes } = useMemo(
    () =>
      applyHighlight(
        rfNodes,
        rfEdges,
        activeNode,
        // 上次视觉态 = 上次 activeNode 在当前图形状上的高亮结果（纯函数
        // 重算，拖拽/布局变化时集合自动跟随）。prev 与本次相同（连续
        // hover 同一节点/选中未变）时无对照需要。
        prevActiveNode === activeNode
          ? undefined
          : applyHighlight(rfNodes, rfEdges, prevActiveNode)
      ),
    [rfEdges, rfNodes, activeNode, prevActiveNode]
  )

  const relevantRuns = useMemo(
    () => filterRelevantRuns(runs, nodes),
    [runs, nodes]
  )

  // #417：图有节点但没有边时布局退化为稳定网格（见 dagLayout.ts），用户
  // 观感是「节点离散/疑似丢失」。这里给一个不遮挡画布的轻量提示，指明是
  // 边数据缺失而非节点缺失；有部分边的图（个别孤立节点）不打扰。
  const showMissingEdgesHint = nodes.length > 0 && edges.length === 0

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
        {showMissingEdgesHint && (
          <span className={styles.missingEdgesBadge} role="status">
            边数据缺失：节点按稳定网格排列
          </span>
        )}
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
