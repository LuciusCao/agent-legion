import { useState } from 'react'
import { useNavigate, Outlet } from 'react-router-dom'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { ErrorFallback } from '../components/ErrorFallback'

/**
 * 页面级错误隔离（#271）：页面子树崩溃只降级为局部错误 UI，布局 shell
 * （AppBar/导航）仍可用。「重试」递增 pageKey 驱动 ErrorBoundary 整棵
 * remount（局部重建，不动 shell）；「返回上一页」走 navigate(-1)。
 * chunk 加载失败（发版后旧 hash 失效）无法靠局部 remount 自愈——React.lazy
 * 缓存 rejected promise，重试只会重抛同一错误——因此同样透传
 * reloadOnChunkError 整页刷新一次（sessionStorage 标记防循环，与 App 层
 * 边界共享同一标记，全局最多 reload 一次）。
 */
export function WorkspacePageOutlet() {
  const navigate = useNavigate()
  const [pageKey, setPageKey] = useState(0)
  return (
    <ErrorBoundary
      key={pageKey}
      reloadOnChunkError
      fallback={
        <ErrorFallback
          title="页面出错了"
          description="当前页面渲染发生异常，其他功能不受影响，可重试或返回上一页。"
          onRetry={() => setPageKey((k) => k + 1)}
          onBack={() => navigate(-1)}
        />
      }
    >
      <Outlet />
    </ErrorBoundary>
  )
}
