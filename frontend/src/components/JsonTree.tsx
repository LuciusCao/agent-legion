import { useState, useCallback } from 'react'
import JsonView from 'react18-json-view'
import 'react18-json-view/src/style.css'
import 'react18-json-view/src/dark.css'
import styles from './JsonTree.module.css'

export interface JsonTreeProps {
  data: unknown
}

export function JsonTree({ data }: JsonTreeProps) {
  const [collapsed, setCollapsed] = useState(false)
  // Bumped on every toolbar click to re-apply `collapsed` even when its
  // value is unchanged (the user may have toggled nodes manually meanwhile).
  const [epoch, setEpoch] = useState(0)

  const expandAll = useCallback(() => {
    setCollapsed(false)
    setEpoch((n) => n + 1)
  }, [])

  const collapseAll = useCallback(() => {
    setCollapsed(true)
    setEpoch((n) => n + 1)
  }, [])

  return (
    <div className={styles.tree}>
      <div className={styles.toolbar}>
        <button
          type="button"
          className={styles.toolbarButton}
          onClick={expandAll}
        >
          全部展开
        </button>
        <button
          type="button"
          className={styles.toolbarButton}
          onClick={collapseAll}
        >
          全部折叠
        </button>
      </div>
      <JsonView
        key={epoch}
        src={data}
        dark
        theme="vscode"
        collapsed={collapsed}
        collapseObjectsAfterLength={Infinity}
        collapseStringsAfterLength={Infinity}
        displayArrayIndex={false}
        enableClipboard={false}
        editable={false}
        style={{ background: 'transparent', fontSize: 'inherit' }}
      />
    </div>
  )
}
