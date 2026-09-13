import { useEffect, useId } from 'react'
import { Link } from 'react-router-dom'
import { MenuItem, TextField } from '@mui/material'
import { ConnectionKeyDatalist, useConnectionKeys } from './connectionOptions'
import styles from './ConnectionKeyField.module.css'

/**
 * 受控的连接 Key 字段（添加条目对话框 ref 面板，#419）：候选来自 key-only
 * 端点（任意登录用户可读）；只有一个 key 时默认选中；端点失败/未返回时
 * 降级为可手写的文本框（datalist 候选仍挂上，接口恢复后重开对话框即回到
 * 选择形态）。实例没有任何连接时（#593）字段下方渲染指向
 * /admin/settings#connections 的管理员入口链接，空态不留死胡同。
 */
export function ConnectionKeyField(props: {
  connectionKey: string
  onConnectionKeyChange: (value: string) => void
}) {
  const { connectionKey, onConnectionKeyChange } = props
  const { options: keys, ready } = useConnectionKeys()
  const datalistId = useId()
  // 两个形态（手写文本框 / MUI select）共享的字段 props。
  const fieldProps = {
    label: '连接 Key',
    value: connectionKey,
    onChange: (event: { target: { value: string } }) =>
      onConnectionKeyChange(event.target.value),
    fullWidth: true,
  }

  // 唯一候选且用户尚未填写时默认选中；选完不覆盖，避免吞掉用户清空
  // 重写的动作。
  useEffect(() => {
    if (ready && keys.length === 1 && connectionKey === '') {
      onConnectionKeyChange(keys[0])
    }
  }, [ready, keys, connectionKey, onConnectionKeyChange])

  if (!ready) {
    return (
      <>
        <TextField
          {...fieldProps}
          placeholder="workflow 绑定的外部服务连接 key"
          inputProps={{ list: datalistId }}
        />
        <ConnectionKeyDatalist id={datalistId} options={keys} />
      </>
    )
  }
  return (
    <>
      <TextField select {...fieldProps}>
        {keys.length === 0 && (
          <MenuItem value="" disabled>
            （实例还没有外部服务连接）
          </MenuItem>
        )}
        {keys.map((key) => (
          <MenuItem key={key} value={key}>
            {key}
          </MenuItem>
        ))}
      </TextField>
      {keys.length === 0 && (
        <div className={styles.emptyHint}>
          需要管理员先到
          <Link to="/admin/settings#connections">全局设置 · 外部服务连接</Link>
          配置。
        </div>
      )}
    </>
  )
}
