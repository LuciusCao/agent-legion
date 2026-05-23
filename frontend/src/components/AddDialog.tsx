import { useState, useRef, useCallback } from "react";
import { useUiStore } from "../stores/uiStore";
import { api } from "../api";
import { parseResourceIds } from "../helpers";
import type { AddResult, VideoItem } from "../types";

export function AddDialog() {
  const { addDialogOpen, addContentType, closeAddDialog, setAddContentType } = useUiStore();
  const [results, setResults] = useState<AddResult[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const input = textareaRef.current?.value || "";
      const ids = parseResourceIds(input);
      if (ids.length === 0) return;
      setIsSubmitting(true);
      try {
        const response = await api<{ videos: VideoItem[]; results: AddResult[] }>("/api/videos", {
          method: "POST",
          body: JSON.stringify({
            items: ids.map((externalId) => ({ content_type: addContentType, external_id: externalId })),
          }),
        });
        setResults(response.results);
        if (textareaRef.current) textareaRef.current.value = "";
      } finally {
        setIsSubmitting(false);
      }
    },
    [addContentType]
  );

  const handleClose = useCallback(() => {
    setResults([]);
    closeAddDialog();
  }, [closeAddDialog]);

  if (!addDialogOpen) return null;

  return (
    <md-dialog open onClosed={handleClose} style={{ minWidth: "520px", "--md-dialog-container-color": "#ffffff" } as React.CSSProperties}>
      <div slot="headline">添加资源</div>
      <form slot="content" id="add-resource-form" onSubmit={handleSubmit}>
        <div style={{ display: "grid", gap: "16px", minWidth: "460px" }}>
          <div style={{ display: "flex", gap: "8px" }}>
            <md-outlined-button
              className={addContentType === "knowledge" ? "type-btn active" : "type-btn"}
              onClick={() => setAddContentType("knowledge")}
            >
              知识点
            </md-outlined-button>
            <md-outlined-button
              className={addContentType === "question" ? "type-btn active" : "type-btn"}
              onClick={() => setAddContentType("question")}
            >
              题目
            </md-outlined-button>
          </div>
          <md-outlined-text-field
            ref={textareaRef}
            type="textarea"
            rows={8}
            label="资源 ID"
            placeholder="一行一个知识点code，或者一行多个知识点用逗号分割"
          />
          {results.length > 0 && (
            <div className="add-results">
              {results.map((r, i) => (
                <div key={i} className="add-result">
                  <span>{r.external_id}</span>
                  <span>{r.status}</span>
                  <span>{r.message || ""}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </form>
      <div slot="actions">
        <md-text-button type="button" onClick={handleClose}>取消</md-text-button>
        <md-filled-button type="submit" form="add-resource-form" disabled={isSubmitting}>
          {isSubmitting ? "处理中..." : "加入队列"}
        </md-filled-button>
      </div>
    </md-dialog>
  );
}
