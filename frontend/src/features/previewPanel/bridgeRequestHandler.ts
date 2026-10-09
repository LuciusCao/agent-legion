/**
 * 桥 request 的宿主侧方法体（#328 → #1146）：PreviewPanelHost 保留消息
 * 鉴别与分发，这里只实现方法执行。全部只读——返回的都是当前查看者已有权
 * 看到的数据（readArtifactBytes 复用 raw 端点的会话鉴权与 content-type
 * 白名单，内存护栏与上限取值见 api/jobArtifactBytes.ts）。
 */
import { fetchJobArtifact, fetchJobArtifactRawBytes } from '../../api'
import type { JobDetail } from '../../types/jobTypes'

/** 宿主回包通道：与 request 按 id 配对（实现见 PreviewPanelHost）。 */
export type BridgeResponder = (
  id: number,
  ok: boolean,
  payload?: unknown,
  error?: string
) => void

export async function handleBridgeRequest(
  id: number,
  method: string,
  params: { name?: string } | undefined,
  jobId: string,
  detail: JobDetail | undefined,
  respond: BridgeResponder
): Promise<void> {
  try {
    switch (method) {
      case 'listArtifacts':
        respond(id, true, detail?.artifacts ?? [])
        return
      case 'getJobDetail':
        respond(id, true, detail ?? null)
        return
      case 'readArtifact': {
        if (!params?.name) {
          respond(id, false, undefined, 'readArtifact requires params.name')
          return
        }
        respond(id, true, await fetchJobArtifact(jobId, params.name))
        return
      }
      case 'readArtifactBytes': {
        if (!params?.name) {
          respond(
            id,
            false,
            undefined,
            'readArtifactBytes requires params.name'
          )
          return
        }
        respond(id, true, await fetchJobArtifactRawBytes(jobId, params.name))
        return
      }
      default:
        respond(id, false, undefined, `unknown bridge method: ${method}`)
    }
  } catch (error) {
    respond(
      id,
      false,
      undefined,
      error instanceof Error ? error.message : String(error)
    )
  }
}
