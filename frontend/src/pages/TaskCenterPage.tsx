import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useUiStore } from '../stores/uiStore'
import {
  runBatchToCard,
  type TaskCardModel,
} from '../features/taskCenter/taskCardModel'
import { TaskCard, type TaskCardActions } from '../features/taskCenter/TaskCard'
import {
  useActiveRunBatches,
  useRunBatchActions,
  useRunBatchWaterfall,
} from '../features/taskCenter/useRunBatches'
import styles from '../features/taskCenter/TaskCenterPage.module.css'

/** 卡片底部「已进行 / 等待」行的重绘节拍（数据本身由查询轮询刷新）。 */
const CLOCK_TICK_MS = 10_000

function useNowMs(): number {
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), CLOCK_TICK_MS)
    return () => window.clearInterval(timer)
  }, [])
  return nowMs
}

/**
 * 任务中心（#1157 phase3）：workspace 级批量操作进度页。数据源为
 * run_batches 三视图（active 全量 / ended、archived keyset 瀑布流）；
 * 卡片模型 source-agnostic，M52 的 job_batch_operations 复用本页与
 * TaskCard（taskCardModel.ts 的 kind 扩展点）。
 */
export default function TaskCenterPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const { showToast } = useUiStore()
  const nowMs = useNowMs()
  const archivedView = searchParams.get('view') === 'archived'

  const activeQuery = useActiveRunBatches(workspaceId)
  const endedQuery = useRunBatchWaterfall(workspaceId, 'ended', !archivedView)
  const archivedQuery = useRunBatchWaterfall(
    workspaceId,
    'archived',
    archivedView
  )
  const actions = useRunBatchActions(workspaceId)

  const activeCards = useMemo(
    () => (activeQuery.data?.batches ?? []).map(runBatchToCard),
    [activeQuery.data]
  )
  const endedCards = useMemo(
    () =>
      (endedQuery.data?.pages ?? []).flatMap((page) =>
        page.batches.map(runBatchToCard)
      ),
    [endedQuery.data]
  )
  const archivedCards = useMemo(
    () =>
      (archivedQuery.data?.pages ?? []).flatMap((page) =>
        page.batches.map(runBatchToCard)
      ),
    [archivedQuery.data]
  )

  const cardActions: TaskCardActions = {
    onPause: (id) => actions.pause.mutate(id),
    onResume: (id) => actions.resume.mutate(id),
    onCancel: (id) => actions.cancel.mutate(id),
    onArchive: (id) =>
      actions.archive.mutate(id, {
        onSuccess: () =>
          showToast(
            '已归档。记录移到任务中心右上角「已归档」，随时可以查看。',
            'success'
          ),
      }),
    onViewJobs: () => navigate(`/workspaces/${workspaceId}`),
  }

  if (archivedView) {
    return (
      <div className={styles.page}>
        <div className={styles.summary}>
          <h1>已归档</h1>
          <span className={styles.summaryNote}>
            归档记录保留 90 天，超期自动清理 ·
          </span>
          <button
            className={styles.linkBtn}
            onClick={() => setSearchParams({})}
          >
            ← 返回任务中心
          </button>
        </div>
        <div className={styles.archivedBanner}>
          这里是「收起来」的记录：结束超过 7
          天自动归档，或在任务中心手动点「归档」。仅查看，不能操作；数据本身不受影响。
        </div>
        <div className={styles.cardGrid}>
          {archivedCards.map((card) => (
            <TaskCard key={card.id} card={card} nowMs={nowMs} readonly />
          ))}
        </div>
        <div className={styles.loadMoreWrap}>
          {archivedQuery.hasNextPage ? (
            <button
              className={styles.linkBtn}
              disabled={archivedQuery.isFetchingNextPage}
              onClick={() => archivedQuery.fetchNextPage()}
            >
              {archivedQuery.isFetchingNextPage ? '加载中…' : '加载更早的记录'}
            </button>
          ) : (
            archivedCards.length > 0 && (
              <span className={styles.noMore}>已加载全部记录</span>
            )
          )}
        </div>
        {!archivedQuery.isLoading && archivedCards.length === 0 && (
          <div className={styles.emptyState}>
            还没有归档记录。
            <br />
            终态卡片在任务中心点「归档」、或结束超过 7 天后，会移到这里。
          </div>
        )}
      </div>
    )
  }

  const runningCount = activeCards.filter(
    (card) => card.status === 'running' || card.status === 'paused'
  ).length
  const queuedCount = activeCards.length - runningCount
  const showEmpty =
    !activeQuery.isLoading &&
    !endedQuery.isLoading &&
    activeCards.length === 0 &&
    endedCards.length === 0

  return (
    <div className={styles.page}>
      <div className={styles.summary}>
        <h1>任务中心</h1>
        <div className={styles.chips}>
          <span>
            进行中 <b>{runningCount}</b>
          </span>
          <span>
            排队中 <b>{queuedCount}</b>
          </span>
        </div>
        <span className={styles.summaryNote}>结束超过 7 天自动归档 ·</span>
        <button
          className={styles.linkBtn}
          onClick={() => setSearchParams({ view: 'archived' })}
        >
          已归档 →
        </button>
      </div>

      {showEmpty ? (
        <div className={styles.emptyState}>
          当前没有批量操作记录。
          <br />
          大批量添加条目（超过 1,000 条）或对任务做多选批量操作（重跑 / 升级 /
          打包 / 删除）后，进度会出现在这里；
          <br />
          结束超过 7 天的记录自动移入「已归档」，不会一直堆在界面上。
        </div>
      ) : (
        <>
          {activeCards.length > 0 && (
            <>
              <div className={styles.sectionTitle}>
                进行中与排队
                <span className={styles.countNote}>
                  · 进行中按发起时间倒序，排队按提交顺序
                </span>
              </div>
              <div className={styles.cardGrid}>
                {activeCards.map((card: TaskCardModel) => (
                  <TaskCard
                    key={card.id}
                    card={card}
                    nowMs={nowMs}
                    actions={cardActions}
                  />
                ))}
              </div>
            </>
          )}
          {endedCards.length > 0 && (
            <>
              <div className={styles.sectionTitle}>
                已结束
                <span className={styles.countNote}>
                  · 按结束时间倒序 · 首屏只加载最近几条，点击加载更早
                </span>
              </div>
              <div className={styles.cardGrid}>
                {endedCards.map((card) => (
                  <TaskCard
                    key={card.id}
                    card={card}
                    nowMs={nowMs}
                    actions={cardActions}
                  />
                ))}
              </div>
              <div className={styles.loadMoreWrap}>
                {endedQuery.hasNextPage ? (
                  <button
                    className={styles.linkBtn}
                    disabled={endedQuery.isFetchingNextPage}
                    onClick={() => endedQuery.fetchNextPage()}
                  >
                    {endedQuery.isFetchingNextPage
                      ? '加载中…'
                      : '加载更早的记录'}
                  </button>
                ) : (
                  <span className={styles.noMore}>
                    已加载全部记录 · 结束超过 7 天的会自动移入「已归档」
                  </span>
                )}
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
