import { Typography } from '@mui/material'

type Props = {
  advisories: string[]
  contentStale: boolean
}

export function WorkflowPublishReviewDialogAdvisories({
  advisories,
  contentStale,
}: Props) {
  return (
    <>
      {contentStale && (
        <Typography variant="body2" color="error" sx={{ mt: 2 }}>
          草稿内容在你审阅期间已被更新（可能来自 Agent
          自动保存）——请关闭后重新审阅再发布
        </Typography>
      )}
      {advisories.length > 0 && (
        <Typography
          variant="body2"
          color="warning.main"
          sx={{ mt: 2, whiteSpace: 'pre-line' }}
          data-testid="publish-advisories"
        >
          {advisories.join('\n')}
        </Typography>
      )}
    </>
  )
}
