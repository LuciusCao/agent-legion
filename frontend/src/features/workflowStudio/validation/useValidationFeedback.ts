import { useEffect, useState } from 'react'
import { useUiStore } from '../../../stores/uiStore'

/** 校验/发布的结果反馈：校验结果写 validation state（「变更」页展示）+
 * 弹 toast。草稿再编辑后旧结果即失效。
 * 轮 7 P1：发布结果与校验通道分离——publishDraft 走 notify（纯 toast），
 * 不再写 validationMessage（否则「保存失败：…」污染校验通道，canPublish
 * 因值非「校验通过」永久封死发布）。 */
export function useValidationFeedback(
  definitionYaml: string,
  workspaceId?: string
) {
  const [validationErrors, setValidationErrors] = useState<string[]>([])
  const [validationMessage, setValidationMessage] = useState('')
  // 草稿再编辑或 workspace 切换后，上一次校验/发布的结果即失效（轮 7 P2：
  // 校验按 workspace 身份 + YAML 双重绑定，同 YAML 的跨 workspace 复用页面
  // 不得继承旧结果），避免陈旧状态挂在「变更」页。
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 草稿变化是旧结果的有意失效点
    setValidationErrors([])
    setValidationMessage('')
  }, [definitionYaml, workspaceId])

  function report(
    errors: string[],
    message: string,
    toastType: 'success' | 'error'
  ) {
    reportSilent(errors, message)
    useUiStore.getState().showToast(message, toastType)
  }

  /* #804 定案：草稿保存成功后的自动校验静默执行（不弹 toast、不开抽屉），
     结果只写 validation state 驱动左岛状态 chip 与变更抽屉内容。 */
  function reportSilent(errors: string[], message: string) {
    setValidationErrors(errors)
    setValidationMessage(message)
  }

  // 轮 7 P1：发布结果的纯 toast 通道（不写 validation state）。
  function notify(message: string, toastType: 'success' | 'error') {
    useUiStore.getState().showToast(message, toastType)
  }

  // 轮 6 H3：传输失败终态的显式重试——清空结果即触发自动校验重跑
  // （useDraftAutoValidation 的 settled+未校验守卫）。
  function clear() {
    setValidationErrors([])
    setValidationMessage('')
  }

  return {
    validationErrors,
    validationMessage,
    report,
    reportSilent,
    clear,
    notify,
  }
}
