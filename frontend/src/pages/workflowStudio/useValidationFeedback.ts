import { useEffect, useState } from 'react'
import { useUiStore } from '../../stores/uiStore'

/** 校验/发布的结果反馈：结果写 validation state（「变更」页展示）并弹
 * toast——选中节点 + Agent 展开时「变更」视图被 display:none，仅写
 * validationMessage 用户会完全看不到结果。草稿再编辑后旧结果即失效。 */
export function useValidationFeedback(definitionYaml: string) {
  const [validationErrors, setValidationErrors] = useState<string[]>([])
  const [validationMessage, setValidationMessage] = useState('')
  // 草稿再编辑后，上一次校验/发布的结果即失效，避免陈旧状态挂在「变更」页。
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 草稿变化是旧结果的有意失效点
    setValidationErrors([])
    setValidationMessage('')
  }, [definitionYaml])

  function report(
    errors: string[],
    message: string,
    toastType: 'success' | 'error'
  ) {
    setValidationErrors(errors)
    setValidationMessage(message)
    useUiStore.getState().showToast(message, toastType)
  }

  return { validationErrors, validationMessage, report }
}
