declare module '*.html?raw' {
  const content: string
  export default content
}

declare module '*?url' {
  const url: string
  export default url
}

// jsdom 没有 bundled 类型（项目未引入 @types/jsdom）；builtin bundle 的
// 行为测试只需要 JSDOM 构造器与 window 的一小块结构，声明最小表面。
declare module 'jsdom' {
  export type JsdomWindow = Window
  export class JSDOM {
    constructor(
      html: string,
      options?: {
        runScripts?: 'dangerously'
        beforeParse?: (window: JsdomWindow) => void
      }
    )
    readonly window: JsdomWindow
  }
}
