import { fetchJobFacets, fetchJobsSnapshot } from '../../../api'
import type { JobSummary } from '../../../types/jobTypes'
import { toJobListFilterParams } from '../listFilterParams'
import type { JobState, JobStoreSet } from '../state'
import { failJobFetch, resetJobListForFilterChange } from './fetch'
import { applyJobPatchBatchUpdate } from './patchActions'
import { setJobsSnapshotUpdate } from './snapshotActions'

export const PAGE_SIZE = 500

// 重拉上限：在途 patch 已进缓冲、revision 冻结，响应几乎必然一次应用
// 成功；重拉只是「并发整页快照先落地推进 revision」的安全网（#1189
// codex P1-a/P1-c）。
const MAX_REFRESH_ATTEMPTS = 3

export function setJobsPageUpdate(
  state: JobState,
  workspaceId: string,
  revision: number,
  jobs: JobSummary[],
  total: number | null | undefined,
  nextCursor: string | null | undefined
): Partial<JobState> {
  // #1183：水位与内容绑定——快照绝不以高于其内容的 revision 落地（服务端
  // 采样 revision 先于读 jobs；抬水位会让采样后产生的 patch 被 revision
  // 守卫永久丢弃）。在途期间落地了更高 revision patch 的陈旧快照由
  // setJobsSnapshotUpdate 的守卫丢弃：SSE 路径由 pendingEvents 排队覆盖，
  // refreshFirstPage 路径由 store 级 patch 缓冲重放覆盖（P1-a/P1-c）。
  const base = setJobsSnapshotUpdate(state, workspaceId, revision, jobs)
  if (Object.keys(base).length === 0) return {}
  return {
    ...base,
    nextCursor: nextCursor ?? null,
    hasMore: Boolean(nextCursor),
    totalJobs: total ?? null,
    loadingMore: false,
  }
}

// The generation counter invalidates an in-flight refresh when a newer one
// (or a workspace switch via the jobsWorkspaceId guard) supersedes it.
let refreshGeneration = 0

export function createRefreshFirstPage(
  set: JobStoreSet,
  get: () => JobState,
  cancelLoadMore: () => void
) {
  return async function refreshFirstPage(workspaceId: string) {
    if (get().jobsWorkspaceId !== workspaceId) return
    const generation = ++refreshGeneration
    // Cancel any in-flight page append; the list is about to be replaced.
    cancelLoadMore()
    const isCurrent = () =>
      generation === refreshGeneration && get().jobsWorkspaceId === workspaceId
    // 置位 snapshotInFlight：在途期间的 patch 进 pendingPatchBuffer、
    // revision 冻结（见 patchActions），快照以真实 revision 落地后重放
    // 缓冲——不完整基线永不提交（#1189 codex P1-c）。
    set((state) => ({
      ...resetJobListForFilterChange(state),
      snapshotInFlight: true,
      pendingPatchBuffer: [],
    }))
    const filterConfig = get().filterConfig
    const params = toJobListFilterParams(filterConfig)
    // 只有 fetchJobsSnapshot 失败才置 listLoadError（整页错误）；facets
    // 只是计数面板，失败独立降级、绝不动已写入的列表（#1189 codex P1-b）。
    // bail 路径（!isCurrent / filterConfig 变更）不清 snapshotInFlight
    // 与缓冲：所有权已被新一轮 refreshFirstPage 的 reset（重新置位）或
    // resetForWorkspace（清空）接管。
    for (let attempt = 0; attempt < MAX_REFRESH_ATTEMPTS; attempt += 1) {
      // #1189 codex P1-d：新一轮采样前清掉在途期间落下的 listLoadError
      // （缓冲溢出 / 并发 SSE loader 失败）——此后发回的响应是全新采样
      // （revision ≥ 所有已记录 patch、内容完整），可安全应用；apply 步骤
      // 的错误态否决只针对「采样早于 failJobFetch」的本轮响应。否决与清除
      // 组合成自愈：被丢弃 patch 不可重放，靠下一次完整采样整体重建。
      // 自愈是 failJobFetch 的逆操作，必须成对恢复：isLoading 归 true
      // （否则空列表 + 非 loading + 无错误 = 假空白，重拉在途期间引导页/
      // 「暂无任务」闪现，#1183 目标症状在自愈路径复活）并重新武装缓冲
      // （溢出已清掉 snapshotInFlight；不重新武装时持续 patch 流会直连落地
      // 推进 revision，重试快照必被守卫丢弃、结构性走到耗尽——窗口内直连
      // 落地的 patch 由重放守卫幂等处理，语义安全）。
      if (get().listLoadError !== null)
        set({
          listLoadError: null,
          isLoading: true,
          snapshotInFlight: true,
          pendingPatchBuffer: [],
        })
      let page: Awaited<ReturnType<typeof fetchJobsSnapshot>>
      try {
        page = await fetchJobsSnapshot(
          workspaceId,
          PAGE_SIZE,
          undefined,
          params
        )
      } catch (err) {
        if (!isCurrent()) return
        const message =
          err instanceof Error ? err.message : 'Failed to load jobs'
        // 缓冲的 patch 随失败终态丢弃：listLoadError 态恢复必经整页
        // 快照，内容整体重建。
        set({
          isLoading: false,
          listLoadError: message,
          snapshotInFlight: false,
          pendingPatchBuffer: [],
        })
        return
      }
      if (!isCurrent() || get().filterConfig !== filterConfig) return
      // 应用快照 + 按序重放缓冲在同一个 set 内原子完成；快照被守卫
      // 丢弃（并发整页快照先落地）则重拉。
      let applied = false
      set((state) => {
        // 错误态否决（#1189 codex P1-d）：在途期间 failJobFetch 落地（缓冲
        // 溢出或并发 SSE loader 失败）意味着已有 patch 被丢弃且不可重放，
        // 本轮响应的采样可能早于那些被丢弃的 patch——否决本轮（applied
        // 保持 false → continue 重拉），不让陈旧内容覆盖并清掉错误态。
        if (state.listLoadError !== null) return {}
        const base = setJobsPageUpdate(
          state,
          workspaceId,
          page.revision,
          page.jobs,
          page.total,
          page.next_cursor
        )
        if (Object.keys(base).length === 0) return {}
        applied = true
        let acc: Partial<JobState> = base
        let merged = { ...state, ...base }
        for (const buffered of state.pendingPatchBuffer) {
          const update = applyJobPatchBatchUpdate(
            merged,
            workspaceId,
            buffered.revision,
            buffered.jobs,
            buffered.deletedJobIds
          )
          if (update) {
            acc = { ...acc, ...update }
            merged = { ...merged, ...update }
          }
        }
        return { ...acc, snapshotInFlight: false, pendingPatchBuffer: [] }
      })
      if (!applied) continue
      const facets = await fetchJobFacets(workspaceId, params).catch(() => null)
      if (!facets || !isCurrent() || get().filterConfig !== filterConfig) {
        return
      }
      set({ facets })
      return
    }
    // 重拉仍被并发整页快照抢跑耗尽（极端罕见）：诚实报错（错误页有
    // 「重试」按钮），不提交半应用状态。
    if (isCurrent()) {
      set((state) => ({
        ...failJobFetch(workspaceId, '列表刷新竞争未收敛，请重试')(state),
        snapshotInFlight: false,
        pendingPatchBuffer: [],
      }))
    }
  }
}
