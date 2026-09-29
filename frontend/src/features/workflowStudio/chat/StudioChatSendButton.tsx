import { MaterialIcon } from '../../../components/MaterialIcon'
import styles from './StudioChatComposer.module.css'

/** composer 工具行的发送按钮（#795 收尾从 StudioChatComposer 拆出保体积
 * 预算）：圆形 ↑ 图标（对齐 ChatGPT/Claude/Kimi 的统一做法），输入为空
 * 禁用置灰（禁用态由调用方按 trim 后内容与会话 disabled 合成）；运行中
 * 不换「排队」文案——点击=入队，队列提示由队列条承担，按钮形态不变。 */
export function StudioChatSendButton(props: {
  busy: boolean
  disabled: boolean
  onSend: () => void
}) {
  return (
    <button
      type="button"
      className={styles.sendButton}
      aria-label="发送"
      title={props.busy ? '发送（运行中将进入队列）' : '发送'}
      disabled={props.disabled}
      onClick={props.onSend}
    >
      <MaterialIcon name="arrow_upward" sx={{ fontSize: 18 }} />
    </button>
  )
}
