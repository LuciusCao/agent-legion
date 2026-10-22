import { useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useUiStore } from '../stores/uiStore'
import type { TaskCardModel } from '../features/taskCenter/taskCardModel'
import { TaskCard, type TaskCardActions } from '../features/taskCenter/TaskCard'
import { useRunBatchActions } from '../features/taskCenter/useRunBatches'
import { useJobBatchOpArchive } from '../features/taskCenter/useJobBatchOpListing'
import { useTaskCenterCards } from '../features/taskCenter/useTaskCenterCards'
import { TaskOpResultsDialog } from '../features/taskCenter/TaskOpResultsDialog'
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
 * 任务中心（#1157 phase3 + #946 M52）：workspace 级批量操作进度页。run_batches
 * （批量创建）与 job_batch_operations（重跑/升级/删除/打包等）的卡片在同一
 * 列表按时间混排（active/ended/archived 三视图统一）；job-batch-op 卡片的
 * 暂停/取消控制面本期不外露（M52 裁决），终态给逐条结果/查看任务/归档。
 */
export default function TaskCenterPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const { showToast } = useUiStore()
  const nowMs = useNowMs()
  const archivedView = searchParams.get('view') === 'archived'

  const cards = useTaskCenterCards(workspaceId, archivedView)
  const runBatchActions = useRunBatchActions(workspaceId)
  const opArchive = useJobBatchOpArchive(workspaceId)
  const [resultsOp, setResultsOp] = useState<{
    id: string
    title: string
  } | null>(null)

  const onArchived = () =>
    showToast(
      '已归档。记录移到任务中心右上角「已归档」，随时可以查看。',
      'success'
    )

  /** 按卡片来源分发操作：run-batch 有暂停/继续/取消控制面；job-batch-op
   * 只读进度 + 终态归档（M61）与逐条结果。 */
  const actionsFor = (card: TaskCardModel): TaskCardActions =>
    card.kind === 'run-batch'
      ? {
          onPause: (id) => runBatchActions.pause.mutate(id),
          onResume: (id) => runBatchActions.resume.mutate(id),
          onCancel: (id) => runBatchActions.cancel.mutate(id),
          onArchive: (id) =>
            runBatchActions.archive.mutate(id, { onSuccess: onArchived }),
          onViewJobs: () => navigate(`/workspaces/${workspaceId}`),
        }
      : {
          onArchive: (id) => opArchive.mutate(id, { onSuccess: onArchived }),
          onViewJobs: () => navigate(`/workspaces/${workspaceId}`),
          onViewResults: (id) => setResultsOp({ id, title: card.title }),
        }

  const renderCards = (list: TaskCardModel[], readonly = false) => (
    <div className={styles.cardGrid}>
      {list.map((card) => (
        <TaskCard
          key={card.id}
          card={card}
          nowMs={nowMs}
          readonly={readonly}
          actions={readonly ? {} : actionsFor(card)}
        />
      ))}
    </div>
  )

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
        {renderCards(cards.archivedCards, true)}
        <div className={styles.loadMoreWrap}>
          {cards.archived.hasNextPage ? (
            <button
              className={styles.linkBtn}
              disabled={cards.archived.isFetchingNextPage}
              onClick={() => cards.archived.fetchNextPage()}
            >
              {cards.archived.isFetchingNextPage ? '加载中…' : '加载更早的记录'}
            </button>
          ) : (
            cards.archivedCards.length > 0 && (
              <span className={styles.noMore}>已加载全部记录</span>
            )
          )}
        </div>
        {!cards.isArchivedLoading && cards.archivedCards.length === 0 && (
          <div className={styles.emptyState}>
            还没有归档记录。
            <br />
            终态卡片在任务中心点「归档」、或结束超过 7 天后，会移到这里。
          </div>
        )}
      </div>
    )
  }

  const runningCount = cards.activeCards.filter(
    (card) => card.status === 'running' || card.status === 'paused'
  ).length
  const queuedCount = cards.activeCards.length - runningCount
  const showEmpty =
    !cards.isLoading &&
    cards.activeCards.length === 0 &&
    cards.endedCards.length === 0

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
          {cards.activeCards.length > 0 && (
            <>
              <div className={styles.sectionTitle}>
                进行中与排队
                <span className={styles.countNote}>
                  · 进行中按发起时间倒序，排队按提交顺序
                </span>
              </div>
              {renderCards(cards.activeCards)}
            </>
          )}
          {cards.endedCards.length > 0 && (
            <>
              <div className={styles.sectionTitle}>
                已结束
                <span className={styles.countNote}>
                  · 按结束时间倒序 · 首屏只加载最近几条，点击加载更早
                </span>
              </div>
              {renderCards(cards.endedCards)}
              <div className={styles.loadMoreWrap}>
                {cards.ended.hasNextPage ? (
                  <button
                    className={styles.linkBtn}
                    disabled={cards.ended.isFetchingNextPage}
                    onClick={() => cards.ended.fetchNextPage()}
                  >
                    {cards.ended.isFetchingNextPage
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
      <TaskOpResultsDialog
        workspaceId={workspaceId ?? ''}
        opId={resultsOp?.id ?? null}
        title={resultsOp?.title ?? ''}
        open={resultsOp !== null}
        onClose={() => setResultsOp(null)}
      />
    </div>
  )
}
