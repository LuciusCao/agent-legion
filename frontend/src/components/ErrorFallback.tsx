import { Alert, AlertTitle, Button } from '@mui/material'
import { MaterialIcon } from './MaterialIcon'

export interface ErrorFallbackProps {
  /** 兜底 UI 的标题；App 层（整页崩溃）与页面层（局部隔离）措辞不同。 */
  title: string
  /** 展示给用户的错误描述，通常含错误消息文本。 */
  description: string
  /** 「重试」动作：传 null 时不渲染该按钮（App 层只有「刷新整页」）。 */
  onRetry?: (() => void) | null
  /** 「返回上一页」动作：传 null 时不渲染该按钮。 */
  onBack?: (() => void) | null
}

/**
 * ErrorBoundary 的兜底 UI（#271）：App 层用于整页崩溃（只有「刷新整页」），
 * WorkspaceLayout 层用于页面级隔离（「重试」局部 remount +「返回上一页」）。
 * 与项目现有错误提示风格保持一致：MUI Alert + role="alert" +
 * theme 的 error 色（#ba1a1a，见 theme.ts / 各 *.module.css 的 .error）。
 */
export function ErrorFallback({
  title,
  description,
  onRetry,
  onBack,
}: ErrorFallbackProps) {
  return (
    <Alert
      severity="error"
      icon={<MaterialIcon name="error" />}
      role="alert"
      sx={{ m: 2 }}
    >
      <AlertTitle>{title}</AlertTitle>
      {description}
      <div>
        {onRetry && (
          <Button size="small" color="inherit" onClick={onRetry}>
            重试
          </Button>
        )}
        {onBack && (
          <Button size="small" color="inherit" onClick={onBack}>
            返回上一页
          </Button>
        )}
      </div>
    </Alert>
  )
}

/** App 层兜底：整页崩溃时唯一安全的恢复动作是刷新整页。 */
export function AppErrorFallback() {
  return (
    <ErrorFallback
      title="页面出错了"
      description="页面渲染发生异常，请刷新重试；若持续出现请联系管理员。"
      onRetry={null}
      onBack={null}
    />
  )
}
