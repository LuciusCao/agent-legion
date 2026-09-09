// 容量旋钮字段组（#509 agent_enqueue 入队池、#554 result 解包进程池、
// #561 Worker 在线标记写入节流）。
// 从 instanceSettingsFields.ts 拆出以控制体积预算（主表有 #521 豁免
// ceiling，不可再上抬）；三者都是实例级、重启生效的容量调参。

import type { FieldGroup } from './instanceSettingsFieldTypes'

export const CAPACITY_FIELD_GROUPS: FieldGroup[] = [
  {
    title: '队列与解包容量',
    fields: [
      // #509：Host 入队线程池；每个入队闭包约 1s IO，吞吐随 workers 线性扩。
      {
        path: 'agent_enqueue.workers',
        label: 'Agent 入队线程数',
        integer: true,
        max: 256,
      },
      {
        path: 'agent_enqueue.max_pending',
        label: 'Agent 入队排队上限',
        integer: true,
      },
      // #554：result 解包进程池尺寸；0 = 自动（min(4, 核数)）。
      {
        path: 'result_unpack.workers',
        label: 'result 解包进程数（0 = 自动）',
        integer: true,
        allowZero: true,
        max: 64,
      },
      // #561：Worker「最近在线」时间的写入节流；说明用用户视角（issue 验收）。
      {
        path: 'agent_claim.worker_touch_interval_seconds',
        label: 'Worker 在线标记写入间隔（秒）',
        integer: false,
        allowZero: true,
        max: 86400,
        hint: 'claim 领取与 result 提交时刷新 Worker『最近在线』时间的最小间隔；窗口内跳过写入，消除小集群下 worker 热行的锁竞争。心跳（每 10 秒）不受此限制，在线状态判定不受影响。0 = 每次都写（恢复 0.7.5 行为）。重启生效。',
      },
    ],
    toggles: [],
  },
]
