import { useState } from 'react'
import styles from './WorkflowStructuredEditor.module.css'

type Props = {
  propKeys: string[]
  onAdd: (name: string) => string | null
  onRemoveSchema: () => void
}

// config_schema 区块的新增属性 + 删整段入口（#418 面板），从
// WorkflowNodeConfigSchemaSection 拆出以守单文件预算。名字校验回调
// 返回错误文案（null = 通过），由父区块负责落草稿。
export function WorkflowNodeSchemaPropertyAdder({
  propKeys,
  onAdd,
  onRemoveSchema,
}: Props) {
  const [newPropName, setNewPropName] = useState('')
  const [newPropError, setNewPropError] = useState('')

  const handleAdd = () => {
    const error = onAdd(newPropName)
    if (error) {
      setNewPropError(error)
      return
    }
    setNewPropName('')
    setNewPropError('')
  }

  return (
    <>
      <div className={styles.field}>
        <span className={styles.fieldLabel}>新增属性</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            aria-label="新增属性名"
            className={styles.fieldInput}
            value={newPropName}
            onChange={(event) => {
              setNewPropName(event.target.value)
              setNewPropError('')
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') handleAdd()
            }}
            placeholder="如 dry_run"
          />
          <button type="button" onClick={handleAdd}>
            新增
          </button>
        </div>
        {newPropError && (
          <span className={styles.fieldHint} role="alert">
            {newPropError}
          </span>
        )}
      </div>
      {propKeys.length > 0 && (
        <button type="button" onClick={onRemoveSchema}>
          删除整段 Schema
        </button>
      )}
    </>
  )
}
