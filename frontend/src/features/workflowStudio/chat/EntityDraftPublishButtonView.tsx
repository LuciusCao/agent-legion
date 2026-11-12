import styles from './StudioChatPanel.module.css'

export function EntityDraftPublishButtonView({
  busy,
  published,
  error,
  onPublish,
}: {
  busy: boolean
  published: boolean
  error: string
  onPublish: () => void
}) {
  const label = published ? '已发布' : busy ? '发布中…' : '发布节点代码'
  return (
    <>
      <button
        type="button"
        className={styles.draftButton}
        disabled={busy || published}
        onClick={onPublish}
      >
        {label}
      </button>
      {error && (
        <div className={styles.draftError} role="alert">
          {error}
        </div>
      )}
    </>
  )
}
