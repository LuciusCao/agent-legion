import { useEffect, useReducer, useRef } from 'react'
import type { MutableRefObject } from 'react'
import { useReactFlow, useStore, useStoreApi } from '@xyflow/react'

/* 镜头平滑移动的时长：够感知「飞过去」又不拖沓。 */
const CENTER_DURATION_MS = 350

/** #667 B2：选中节点变化时把镜头平滑移到该节点——价值在画布外入口的选中
 * （聊天草稿卡、diff 变更列表跳过来的定位）；画布内点击的选中不跟镜头
 * （节点本来就在光标下，镜头再飞一次是打扰），经 clickOriginRef 跳过。
 * 克制约束：仅在「选中 id 或 Studio 定位请求 nonce」变化时触发一次
 * （focusedRef 记录上次定位的 {key, nonce}，拖拽 / hover / 高亮重算等
 * 渲染都不会重复飞行）；取消选中后再次选中同一节点会重新定位。作为 <ReactFlow> 的 children 渲染，复用其内部 store。
 * DagGraph 是共享组件（job 详情等），job 侧选中全部来自画布点击，天然
 * 落在跳过分支，不改变既有观感。 */
export function DagSelectionViewport({
  selectedNode,
  selectionNonce,
  nodesVersion,
  clickOriginRef,
}: {
  selectedNode: string | null
  /* Studio 选择链路的定位请求信号：key 不变而 nonce 变化（已选中节点被
     再次请求定位）时同样触发定位；不传则只按 key 变化定位。 */
  selectionNonce?: number
  /* 节点集合变化信号（DagGraph 的 rfNodes 引用）：选中先于布局同步到达时
     internal node 尚未入 store，等节点集合落地后重试定位。 */
  nodesVersion: readonly unknown[]
  clickOriginRef: MutableRefObject<string | null>
}) {
  const { setCenter, getZoom, getInternalNode } = useReactFlow()
  const storeApi = useStoreApi()
  // 上次定位完成的 {key, nonce}：两者都未变才跳过，拖拽 / hover / 高亮
  // 重算等渲染都不会重复飞行。
  const focusedRef = useRef<{ key: string; nonce: number | undefined } | null>(
    null
  )
  /* 容器尺寸信号：移动端 DAG 面板被 CSS display:none 隐藏时尺寸为零，定位
     被跳过且 selectedNode/nodesVersion 不再变化；切回图面板只改 class。监听
     容器尺寸，恢复非零后让未完成的定位重试（已完成定位由 focusedRef 去重，
     不会重复飞行）。 */
  const [sizeTick, bumpSizeTick] = useReducer((count: number) => count + 1, 0)
  useEffect(() => {
    const domNode = storeApi.getState().domNode
    if (!domNode || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => bumpSizeTick())
    observer.observe(domNode)
    return () => observer.disconnect()
  }, [storeApi])
  /* 选中节点的测量就绪信号：新 ReactFlow 实例以已有 selectedNode 挂载时
     （如选中后打开全屏 DAG），getInternalNode 在 DOM 测量完成前返回、
     measured 为空。订阅 store，测量就绪翻转后重试定位。 */
  const measuredReady = useStore((state) => {
    if (!selectedNode) return false
    const measured = state.nodeLookup.get(selectedNode)?.measured
    return measured?.width != null && measured?.height != null
  })
  useEffect(() => {
    if (!selectedNode) {
      focusedRef.current = null
      // 清除可能遗留的画布点击来源：外部选中并定位后用户再点同一节点会
      // 写入 clickOriginRef，但 focusedRef 去重会让 effect 提前返回不消费
      // 它；不在这里清掉，下次同节点的外部选择会被误判为画布点击而跳过。
      clickOriginRef.current = null
      return
    }
    if (
      focusedRef.current?.key === selectedNode &&
      focusedRef.current.nonce === selectionNonce
    )
      return
    if (clickOriginRef.current === selectedNode) {
      // 画布点击选中的节点必然在视口内（用户刚点过），不移动镜头。
      clickOriginRef.current = null
      focusedRef.current = { key: selectedNode, nonce: selectionNonce }
      return
    }
    const internal = getInternalNode(selectedNode)
    // 节点未入 store（布局同步前）：保留未定位态，nodesVersion 变化时重试。
    if (!internal) return
    // measured 未完成（新实例挂载、DOM 测量前）时宽高会降为 0，镜头定到
    // 节点左上角且标完成后无法校正——保留未定位态，measuredReady 翻转后重试。
    const { width, height } = internal.measured ?? {}
    if (width == null || height == null) return
    // 零尺寸容器（jsdom、隐藏挂载）里镜头不可见，且 d3-zoom 过渡插值以容器
    // 尺寸为分母会产出 NaN 视口，直接跳过——保持未定位态，容器有尺寸后
    // 随节点/选中变化重试。
    const domNode = storeApi.getState().domNode
    if (!domNode || domNode.clientWidth === 0 || domNode.clientHeight === 0) {
      return
    }
    clickOriginRef.current = null
    focusedRef.current = { key: selectedNode, nonce: selectionNonce }
    const { x, y } = internal.internals.positionAbsolute
    void setCenter(x + width / 2, y + height / 2, {
      zoom: getZoom(),
      duration: CENTER_DURATION_MS,
    })
  }, [
    selectedNode,
    selectionNonce,
    nodesVersion,
    sizeTick,
    measuredReady,
    clickOriginRef,
    setCenter,
    getZoom,
    getInternalNode,
    storeApi,
  ])
  return null
}
