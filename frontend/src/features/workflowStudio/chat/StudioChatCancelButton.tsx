import { MaterialIcon } from '../../../components/MaterialIcon'
import styles from './StudioChatComposer.module.css'

/** #787：composer 工具行的取消按钮——次级破坏性动作，仅运行中渲染（调用方
 * 以 onCancel 是否传入控制，不运行时不占位），点击即取消当前运行。
 * #795 收尾：与发送按钮同款的圆形图标按钮（■ stop，弱红色描边），视觉
 * 成对。样式留在 StudioChatComposer.module.css（与发送按钮同行协调）；
 * 独立成组件是为 composer 本体的体积预算。 */
export function StudioChatCancelButton(props: { onCancel: () => void }) {
  return (
    <button
      type="button"
      className={styles.cancelButton}
      aria-label="取消"
      title="取消当前运行"
      onClick={props.onCancel}
    >
      <MaterialIcon name="stop" sx={{ fontSize: 16 }} />
    </button>
  )
}
