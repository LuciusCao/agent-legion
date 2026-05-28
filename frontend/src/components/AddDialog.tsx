import { useState, useRef, useCallback, useEffect } from "react";
import { useUiStore } from "../stores/uiStore";
import { api } from "../api";
import { parseResourceInputs } from "../helpers";
import type { AddResult, VideoItem } from "../types";
import styles from "./AddDialog.module.css";

export function AddDialog() {
  const { addDialogOpen, addContentType, closeAddDialog, setAddContentType } = useUiStore();
  const [results, setResults] = useState<AddResult[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dialogRef = useRef<HTMLElement>(null);

  const handleSubmit = useCallback(async () => {
    const input = textareaRef.current?.value || "";
    const items = parseResourceInputs(input);
    if (items.length === 0) return;
    setIsSubmitting(true);
    try {
      const response = await api<{ videos: VideoItem[]; results: AddResult[] }>("/api/videos", {
        method: "POST",
        body: JSON.stringify({
          items: items.map((item) => ({ content_type: addContentType, external_id: item.external_id, source_uuid: item.source_uuid })),
        }),
      });
      setResults(response.results);
      if (textareaRef.current) textareaRef.current.value = "";
    } finally {
      setIsSubmitting(false);
    }
  }, [addContentType]);

  const handleClose = useCallback(() => {
    setResults([]);
    closeAddDialog();
  }, [closeAddDialog]);

  // md-dialog's 'closed' event is a non-bubbling CustomEvent;
  // React's synthetic event system cannot capture it. Bind directly.
  // Also force-close on unmount / pagehide in case the user navigates away
  // before the close animation finishes (or the page is frozen in bfcache).
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    dialog.addEventListener("closed", handleClose);
    const onHide = () => closeAddDialog();
    window.addEventListener("pagehide", onHide);
    return () => {
      dialog.removeEventListener("closed", handleClose);
      window.removeEventListener("pagehide", onHide);
      closeAddDialog();
    };
  }, [handleClose, closeAddDialog]);

  if (!addDialogOpen) return null;

  const placeholder =
    addContentType === "knowledge"
      ? "一行一个知识点code，例如：x09010402\n或带source_uuid：x09010402,uuid-xxx"
      : "一行一个题目ID，例如：q12345678\n或带source_uuid：q12345678,uuid-xxx";

  return (
    <md-dialog ref={dialogRef} open style={{ minWidth: "520px", "--md-dialog-container-color": "#ffffff" } as React.CSSProperties}>
      <div slot="headline">添加资源</div>
      <div slot="content">
        <div style={{ display: "grid", gap: "16px", minWidth: "460px" }}>
          <div style={{ display: "flex", gap: "8px" }}>
            <md-outlined-button
              className={addContentType === "knowledge" ? `${styles.typeBtn} ${styles.active}` : styles.typeBtn}
              onClick={() => setAddContentType("knowledge")}
            >
              知识点
            </md-outlined-button>
            <md-outlined-button
              className={addContentType === "question" ? `${styles.typeBtn} ${styles.active}` : styles.typeBtn}
              onClick={() => setAddContentType("question")}
            >
              题目
            </md-outlined-button>
          </div>
          <md-outlined-text-field
            ref={textareaRef}
            type="textarea"
            rows={8}
            label={`${addContentType === "knowledge" ? "知识点" : "题目"} ID`}
            placeholder={placeholder}
          />
          {results.length > 0 && (
            <div className={styles.addResults}>
              {results.map((r, i) => (
                <div key={i} className={styles.addResult}>
                  <span>{r.external_id}</span>
                  <span>{r.status}</span>
                  <span>{r.message || ""}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <div slot="actions">
        <md-text-button type="button" onClick={handleClose}>取消</md-text-button>
        <md-filled-button onClick={handleSubmit} disabled={isSubmitting || undefined}>
          {isSubmitting ? "处理中..." : "加入队列"}
        </md-filled-button>
      </div>
    </md-dialog>
  );
}
