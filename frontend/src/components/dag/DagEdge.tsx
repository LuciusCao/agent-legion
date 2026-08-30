import { memo } from 'react'
import {
  BaseEdge,
  EdgeProps,
  getBezierPath,
  type Edge,
} from '@xyflow/react'

export interface DagEdgeData extends Record<string, unknown> {
  /**
   * hover/选中联动的高亮态（#276）：由 DagGraph 在高亮 useMemo 中写入
   * edge.data，本组件读取后自行渲染描边样式。放在 data 而非 edge.style，
   * 是为了让 xyflow 的 EdgeWrapper + 本组件的 memo 在「高亮态未翻转」的
   * 边上保持 props 引用稳定，hover 时不再全量重渲染。
   */
  highlighted?: boolean
}

export type DagEdgeType = Edge<DagEdgeData, 'dagEdge'>

// 与重构前 DagGraph 内联高亮的视觉常量完全一致（详见 DagGraph.tsx 的 #276
// 注释）：markerEnd 颜色由 buildRfEdges 的 markerEnd.color 随 data.highlighted
// 翻转一并重建，这里不再改写 marker。
const STROKE_HIGHLIGHTED = '#1d4ed8'
const STROKE_DEFAULT = '#d1d5db'
const STROKE_WIDTH_HIGHLIGHTED = 3
const STROKE_WIDTH_DEFAULT = 2

export const DagEdge = memo(function DagEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  label,
  labelStyle,
  labelShowBg,
  labelBgStyle,
  labelBgPadding,
  labelBgBorderRadius,
  markerStart,
  style,
  data,
}: EdgeProps<DagEdgeType>) {
  const highlighted = data?.highlighted === true
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  return (
    <BaseEdge
      id={id}
      path={path}
      labelX={labelX}
      labelY={labelY}
      label={label}
      labelStyle={labelStyle}
      labelShowBg={labelShowBg}
      labelBgStyle={labelBgStyle}
      labelBgPadding={labelBgPadding}
      labelBgBorderRadius={labelBgBorderRadius}
      style={{
        ...style,
        stroke: highlighted ? STROKE_HIGHLIGHTED : STROKE_DEFAULT,
        strokeWidth: highlighted
          ? STROKE_WIDTH_HIGHLIGHTED
          : STROKE_WIDTH_DEFAULT,
        opacity: highlighted ? 1 : 0.4,
      }}
      markerStart={markerStart}
    />
  )
})
