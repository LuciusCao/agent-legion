import { useState, useCallback, useRef } from 'react'
import { DagGraph, type DagNode, type DagEdge } from './DagGraph'
import styles from './DagFullscreenDialog.module.css'

interface DagFullscreenDialogProps {
  open: boolean
  nodes: DagNode[]
  edges: DagEdge[]
  onClose: () => void
}

export function DagFullscreenDialog({
  open,
  nodes,
  edges,
  onClose,
}: DagFullscreenDialogProps) {
  const [scale, setScale] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)
  const dragStart = useRef({ x: 0, y: 0, panX: 0, panY: 0 })

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? 0.9 : 1.1
    setScale((s) => Math.min(Math.max(s * delta, 0.3), 3))
  }, [])

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      setDragging(true)
      dragStart.current = {
        x: e.clientX,
        y: e.clientY,
        panX: pan.x,
        panY: pan.y,
      }
    },
    [pan]
  )

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!dragging) return
      const dx = e.clientX - dragStart.current.x
      const dy = e.clientY - dragStart.current.y
      setPan({ x: dragStart.current.panX + dx, y: dragStart.current.panY + dy })
    },
    [dragging]
  )

  const handleMouseUp = useCallback(() => {
    setDragging(false)
  }, [])

  const handleReset = useCallback(() => {
    setScale(1)
    setPan({ x: 0, y: 0 })
  }, [])

  if (!open) return null

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div
        className={styles.dialog}
        onClick={(e) => e.stopPropagation()}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        <div className={styles.header}>
          <span className={styles.title}>DAG 流水线</span>
          <div className={styles.actions}>
            <md-icon-button aria-label="重置视图" onClick={handleReset}>
              <md-icon>center_focus_strong</md-icon>
            </md-icon-button>
            <md-icon-button aria-label="关闭" onClick={onClose}>
              <md-icon>close</md-icon>
            </md-icon-button>
          </div>
        </div>
        <div
          className={styles.canvas}
          style={{ cursor: dragging ? 'grabbing' : 'grab' }}
        >
          <div
            className={styles.transformLayer}
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})`,
              transformOrigin: 'center center',
            }}
          >
            <DagGraph nodes={nodes} edges={edges} />
          </div>
        </div>
      </div>
    </div>
  )
}
