import { statusEvent, type ChatMessage } from './studioChatMessages'
// #695：错误/告警条样式归 AgentChatPanel 骨架（红=statusError，原
// StudioChatPanel.module.css 的 statusWarning 已随语义拆分退役）。
import shellStyles from './AgentChatPanel.module.css'

/** 以告警条渲染的状态事件（StudioChatStatusLine 的姊妹拆分，#694/#695
 * 波次把该文件顶到预算上限后按 #563「拆姊妹文件保体积」惯例抽出）：
 * turn_timeout/empty_turn/run_token_invalidated/error 的共同点是"这轮
 * 出了问题，需要人看见"，文案一律以后端 detail 为唯一来源。 */
export function StatusWarning({ message }: { message: ChatMessage }) {
  const { event, detail } = statusEvent(message)
  if (event === 'turn_timeout') {
    // #693：运行超过平台时限被终止。
    return (
      <div className={shellStyles.statusError} role="alert">
        ⚠ {detail || '运行超过 1 小时已被终止'}
      </div>
    )
  }
  if (event === 'empty_turn') {
    // #694：瞬时零内容的假 end_turn（静默排队签名）——提示重发/继续对话。
    return (
      <div className={shellStyles.statusError} role="alert">
        ⚠ {detail || 'agent 未实际处理这条消息，请稍后重发'}
      </div>
    )
  }
  if (event === 'run_token_invalidated') {
    // run token 过期/吊销：工具通道死亡但聊天主链路仍活着（#411/#558——
    // 会话已被升级为 error，ResumeBar 的「继续对话」直接可达）。
    return (
      <div className={shellStyles.statusError} role="alert">
        ⚠ {detail || '工具通道已失效，点「继续对话」重建即可恢复'}
      </div>
    )
  }
  // event === 'error'
  return (
    <div className={shellStyles.statusError} role="alert">
      ⚠ {detail || 'agent 运行出错'}
    </div>
  )
}
