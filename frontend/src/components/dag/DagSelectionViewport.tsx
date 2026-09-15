import { useEffect, useRef } from 'react'
import type { MutableRefObject } from 'react'
import { useReactFlow, useStoreApi } from '@xyflow/react'

/* 镜头平滑移动的时长：够感知「飞过去」又不拖沓。 */
const CENTER_DURATION_MS = 350

/** #667 B2：选中节点变化时把镜头平滑移到该节点——价值在画布外入口的选中
 * （聊天草稿卡、diff 变更列表跳过来的定位）；画布内点击的选中不跟镜头
 * （节点本来就在光标下，镜头再飞一次是打扰），经 clickOriginRef 跳过。
 * 克制约束：仅在选中 id 变化时触发一次（focusedRef 记录上次定位目标，
 * 拖拽 / hover / 高亮重算等渲染都不会重复飞行）；取消选中后再次选中同一
 * 节点会重新定位。作为 <ReactFlow> 的 children 渲染，复用其内部 store。
 * DagGraph 是共享组件（job 详情等），job 侧选中全部来自画布点击，天然
 * 落在跳过分支，不改变既有观感。 */
export function DagSelectionViewport({
  selectedNode,
  nodesVersion,
  clickOriginRef,
}: {
  selectedNode: string | null
  /* 节点集合变化信号（DagGraph 的 rfNodes 引用）：选中先于布局同步到达时
     internal node 尚未入 store，等节点集合落地后重试定位。 */
  nodesVersion: readonly unknown[]
  clickOriginRef: MutableRefObject<string | null>
}) {
  const { setCenter, getZoom, getInternalNode } = useReactFlow()
  const storeApi = useStoreApi()
  const focusedRef = useRef<string | null>(null)
  useEffect(() => {
    if (!selectedNode) {
      focusedRef.current = null
      return
    }
    if (focusedRef.current === selectedNode) return
    if (clickOriginRef.current === selectedNode) {
      // 画布点击选中的节点必然在视口内（用户刚点过），不移动镜头。
      clickOriginRef.current = null
      focusedRef.current = selectedNode
      return
    }
    const internal = getInternalNode(selectedNode)
    // 节点未入 store（布局同步前）：保留未定位态，nodesVersion 变化时重试。
    if (!internal) return
    // 零尺寸容器（jsdom、隐藏挂载）里镜头不可见，且 d3-zoom 过渡插值以容器
    // 尺寸为分母会产出 NaN 视口，直接跳过——保持未定位态，容器有尺寸后
    // 随节点/选中变化重试。
    const domNode = storeApi.getState().domNode
    if (!domNode || domNode.clientWidth === 0 || domNode.clientHeight === 0) {
      return
    }
    clickOriginRef.current = null
    focusedRef.current = selectedNode
    const { x, y } = internal.internals.positionAbsolute
    const width = internal.measured?.width ?? 0
    const height = internal.measured?.height ?? 0
    void setCenter(x + width / 2, y + height / 2, {
      zoom: getZoom(),
      duration: CENTER_DURATION_MS,
    })
  }, [
    selectedNode,
    nodesVersion,
    clickOriginRef,
    setCenter,
    getZoom,
    getInternalNode,
    storeApi,
  ])
  return null
}
