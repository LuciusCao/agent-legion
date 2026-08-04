import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
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
  if (!open) return null

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: '480px', width: '90vw' } }}
    >
      <DialogTitle>产物文件</DialogTitle>
      <DialogContent>
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
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose}>
          关闭
        </Button>
      </DialogActions>
    </Dialog>
  )
}
