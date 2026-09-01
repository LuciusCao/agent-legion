/**
 * 官方内置预览面板 bundle（issue #328）：question 类 workspace 的默认左栏
 * 体验，兼作用户二开模板。内容在 questionPanel.html（单文件 HTML+CSS+JS），
 * 经 vite `?raw` 原样内联，不进构建管线。
 */
import questionPanelHtml from './questionPanel.html?raw'

export const QUESTION_PANEL_BUNDLE: string = questionPanelHtml
