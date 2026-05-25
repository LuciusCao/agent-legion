import { useCallback } from "react";
import { useUiStore } from "../stores/uiStore";

interface DeleteDialogProps {
  onConfirm: () => void;
}

export function DeleteDialog({ onConfirm }: DeleteDialogProps) {
  const { deleteDialogOpen, closeDeleteDialog } = useUiStore();

  const handleConfirm = useCallback(() => {
    onConfirm();
    closeDeleteDialog();
  }, [onConfirm, closeDeleteDialog]);

  if (!deleteDialogOpen) return null;

  return (
    <md-dialog open onClosed={closeDeleteDialog} style={{ "--md-dialog-container-color": "#ffffff" } as React.CSSProperties}>
      <div slot="headline">确认删除</div>
      <div slot="content">
        <p>确定删除该资源？本地视频和处理产物目录也会删除。</p>
      </div>
      <div slot="actions">
        <md-text-button onClick={closeDeleteDialog}>取消</md-text-button>
        <md-filled-button style={{ "--md-sys-color-primary": "var(--md-sys-color-error)" } as React.CSSProperties} onClick={handleConfirm}>
          删除
        </md-filled-button>
      </div>
    </md-dialog>
  );
}
