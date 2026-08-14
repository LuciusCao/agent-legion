import styles from './StudioChatPanel.module.css'

export type StudioRightPanelTab = 'inspector' | 'chat'

type Props = {
  value: StudioRightPanelTab
  onChange: (tab: StudioRightPanelTab) => void
  onClose: () => void
}

/** Studio 右栏 tab 头：节点配置 / Agent 助手。 */
export function StudioRightPanelTabs(props: Props) {
  return (
    <div className={styles.tabBar} role="tablist" aria-label="Studio 侧栏">
      <button
        type="button"
        role="tab"
        aria-selected={props.value === 'inspector'}
        className={`${styles.tab}${props.value === 'inspector' ? ` ${styles.tabActive}` : ''}`}
        onClick={() => props.onChange('inspector')}
      >
        节点配置
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={props.value === 'chat'}
        className={`${styles.tab}${props.value === 'chat' ? ` ${styles.tabActive}` : ''}`}
        onClick={() => props.onChange('chat')}
      >
        Agent 助手
      </button>
      <button
        type="button"
        className={styles.tabClose}
        aria-label="关闭侧栏"
        onClick={props.onClose}
      >
        ×
      </button>
    </div>
  )
}
