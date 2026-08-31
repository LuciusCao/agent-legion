import { memo } from 'react'
import { BaseEdge, EdgeProps, getBezierPath, type Edge } from '@xyflow/react'

export interface DagEdgeData extends Record<string, unknown> {
  /**
   * hover/选中联动的高亮态（#276）：由 DagGraph 在高亮 useMemo 中写入
   * edge.data，本组件读取后自行渲染描边样式。放在 data 而非 edge.style，
   * 是为了让 xyflow 的 EdgeWrapper + 本组件的 memo 在「高亮态未翻转」的
   * 边上保持 props 引用稳定，hover 时不再全量重渲染。
   *
   * 三态而非双态（Codex review on #285）：
   * - undefined：从未进入过高亮模式（buildRfEdges 的初始值）——保持
   *   buildRfEdges 传入的原始 style（普通边不透明、ghost 边 0.5）；
   * - false：处于高亮模式但本边不在高亮链路上——置灰 0.4；
   * - true：高亮链路上的边——全亮 + 蓝色描边。
   * 若把 undefined 与 false 混同为「不高亮」，常态下所有普通边都会被
   * 强制盖成 0.4 透明度，ghost 边自身的 0.5 也会被覆盖。
   */
  highlighted?: boolean
}

export type DagEdgeType = Edge<DagEdgeData, 'dagEdge'>

// 与重构前 DagGraph 内联高亮的视觉常量完全一致（详见 dagHighlight.ts 的
// #276 注释）；markerEnd 颜色由 dagHighlight 随 highlighted 翻转一并重建，
// 本组件只负责透传 marker（含终点箭头——markerStart/End 都传给 BaseEdge，
// marker 缺失会让方向指示丢失）。
const STROKE_HIGHLIGHTED = '#1d4ed8'
const STROKE_DEFAULT = '#d1d5db'
const STROKE_WIDTH_HIGHLIGHTED = 3
const STROKE_WIDTH_DEFAULT = 2
const OPACITY_DIMMED = 0.4

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
  markerEnd,
  style,
  data,
}: EdgeProps<DagEdgeType>) {
  const highlighted = data?.highlighted === true
  // undefined（常态）不覆盖 style——保留 buildRfEdges 的原始透明度（普通
  // 边不透明、ghost 边 0.5）；只有高亮模式的 false 才是「置灰」。
  const inHighlightMode = data?.highlighted !== undefined
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
      style={
        inHighlightMode
          ? {
              ...style,
              stroke: highlighted ? STROKE_HIGHLIGHTED : STROKE_DEFAULT,
              strokeWidth: highlighted
                ? STROKE_WIDTH_HIGHLIGHTED
                : STROKE_WIDTH_DEFAULT,
              opacity: highlighted ? 1 : OPACITY_DIMMED,
            }
          : style
      }
      markerStart={markerStart}
      markerEnd={markerEnd}
    />
  )
})
