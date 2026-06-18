import { useEffect, useRef, useCallback } from 'react'
import type { CSSProperties } from 'react'
import styles from './ArtifactListDialog.module.css'

export interface ArtifactListDialogProps {
  open: boolean
  artifacts: string[]
  onClose: () => void
  onSelect: (name: string) => void
}

export function ArtifactListDialog({
  open,
  artifacts,
  onClose,
  onSelect,
}: ArtifactListDialogProps) {
  const dialogRef = useRef<
    HTMLElement & { open: boolean; close: () => void; show: () => void }
  >(null)

  const handleCancel = useCallback(
    (event: Event) => {
      // Prevent md-dialog from closing itself on backdrop/scrim click.
      // We drive the open state from React so state stays in sync.
      event.preventDefault()
      onClose()
    },
    [onClose]
  )

  // Sync controlled open state and watch for any external close (e.g. scrim
  // click, ESC) so React state does not get out of sync with the custom
  // element's internal open attribute.
  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return

    const observer = new MutationObserver(() => {
      // Use hasAttribute because the `open` IDL property may not be reflected
      // for unupgraded custom elements (e.g. in jsdom).
      if (!dialog.hasAttribute('open') && open) {
        onClose()
      }
    })

    dialog.addEventListener('cancel', handleCancel)
    observer.observe(dialog, { attributes: true, attributeFilter: ['open'] })

    return () => {
      dialog.removeEventListener('cancel', handleCancel)
      observer.disconnect()
    }
  }, [open, onClose, handleCancel])

  // Imperatively open/close the dialog when the controlled prop changes.
  // In tests <md-dialog> is a plain element, so guard the custom methods.
  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) {
      if (typeof dialog.show === 'function') {
        dialog.show()
      } else {
        dialog.setAttribute('open', '')
      }
    } else if (!open && dialog.open) {
      if (typeof dialog.close === 'function') {
        dialog.close()
      } else {
        dialog.removeAttribute('open')
      }
    }
  }, [open])

  if (!open) return null

  const dialogStyle = {
    '--md-dialog-container-color': '#ffffff',
    maxWidth: '480px',
    width: '90vw',
  } as CSSProperties

  return (
    <md-dialog ref={dialogRef} open onClosed={onClose} style={dialogStyle}>
      <div slot="headline">产物文件</div>
      <div slot="content">
        {artifacts.length === 0 ? (
          <p className={styles.empty}>暂无产物文件</p>
        ) : (
          <ul className={styles.list}>
            {artifacts.map((name) => (
              <li key={name}>
                <button
                  type="button"
                  className={styles.nameBtn}
                  onClick={() => onSelect(name)}
                >
                  {name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div slot="actions">
        <md-text-button onClick={onClose}>关闭</md-text-button>
      </div>
    </md-dialog>
  )
}
