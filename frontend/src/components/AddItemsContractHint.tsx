import {
  ITEM_TYPE_DISPLAY,
  type AcceptedItemType,
} from '../lib/acceptedItemTypes'
import styles from './AddItemsDialog.module.css'

// 规范展示顺序 = ITEM_TYPE_DISPLAY 的 key 顺序（material/ref/bundle/text），
// 与契约数组的声明顺序解耦：改数组顺序不会意外改变展示顺序。
const ITEM_TYPE_ORDER = Object.keys(ITEM_TYPE_DISPLAY) as AcceptedItemType[]

/**
 * 「添加条目」提示条：契约收窄时列出当前工作流接受的提交方式；全接受时
 * 不渲染。`text` 是后加的 opt-in 类型，缺它不算收窄（否则存量工作流会
 * 常驻一条红字），只有旧有类型被排除时才提示。
 */
export function AddItemsContractHint({
  accepted,
}: {
  accepted: readonly AcceptedItemType[]
}) {
  const missing = ITEM_TYPE_ORDER.filter((type) => !accepted.includes(type))
  if (missing.every((type) => type === 'text')) return null
  const labels = ITEM_TYPE_ORDER.filter((type) => accepted.includes(type))
    .map((type) => ITEM_TYPE_DISPLAY[type].label)
    .join('、')
  return (
    <div className={styles.errorHint} data-testid="item-type-hint">
      当前工作流只接受：{labels}
      。其他提交方式已隐藏，可在 Studio 的入口节点调整。
    </div>
  )
}
