import styles from './StudioChatComposer.module.css'

/** #787：composer 工具行的取消按钮——次级破坏性动作（描边弱红、小一号），
 * 仅运行中渲染（调用方以 onCancel 是否传入控制，不运行时不占位），点击即
 * 取消当前运行。样式留在 StudioChatComposer.module.css（与发送按钮同行
 * 协调）；独立成组件是为 composer 本体的体积预算。 */
export function StudioChatCancelButton(props: { onCancel: () => void }) {
  return (
    <button
      type="button"
      className={styles.cancelButton}
      onClick={props.onCancel}
    >
      取消
    </button>
  )
}
