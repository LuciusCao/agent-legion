import { useVideoStore } from '../stores/videoStore'
import { STATUS_FILTER_CONFIG } from '../labels'
import styles from './StatCards.module.css'

const STATUSES = ['queued', 'running', 'failed', 'completed']

export function StatCards() {
  const counts = useVideoStore((state) => state._counts)
  const statusFilter = useVideoStore((state) => state.statusFilter)
  const setStatusFilter = useVideoStore((state) => state.setStatusFilter)
  const packedFilter = useVideoStore((state) => state.packedFilter)
  const setPackedFilter = useVideoStore((state) => state.setPackedFilter)

  const items = [
    { key: 'all', ...STATUS_FILTER_CONFIG.all },
    ...STATUSES.map((s) => ({ key: s, ...STATUS_FILTER_CONFIG[s] })),
  ]

  return (
    <div className={styles.statsPills}>
      {items.map((item) => (
        <div
          key={item.key}
          className={`${styles.statPill} ${
            statusFilter === item.key ? styles.active : ''
          }`}
          onClick={() => setStatusFilter(item.key)}
        >
          <md-icon>{item.icon}</md-icon>
          <span>
            {item.label}（{counts[item.key] ?? 0}）
          </span>
        </div>
      ))}
      {statusFilter === 'completed' && (
        <>
          <span className={styles.pillDivider} />
          <div
            className={`${styles.statPill} ${
              packedFilter === 'packed' ? styles.active : ''
            }`}
            onClick={() =>
              setPackedFilter(packedFilter === 'packed' ? 'all' : 'packed')
            }
          >
            <md-icon>{STATUS_FILTER_CONFIG.packed.icon}</md-icon>
            <span>
              {STATUS_FILTER_CONFIG.packed.label}（{counts.packed ?? 0}）
            </span>
          </div>
          <div
            className={`${styles.statPill} ${
              packedFilter === 'unpacked' ? styles.active : ''
            }`}
            onClick={() =>
              setPackedFilter(packedFilter === 'unpacked' ? 'all' : 'unpacked')
            }
          >
            <md-icon>{STATUS_FILTER_CONFIG.unpacked.icon}</md-icon>
            <span>
              {STATUS_FILTER_CONFIG.unpacked.label}（{counts.unpacked ?? 0}）
            </span>
          </div>
        </>
      )}
    </div>
  )
}
