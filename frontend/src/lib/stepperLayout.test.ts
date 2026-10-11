import { describe, expect, it } from 'vitest'
import {
  STEPPER_MAX_GAP_PX,
  STEPPER_MIN_SEGMENT_PX,
  stepperTrackGap,
} from './stepperLayout'

describe('stepperTrackGap (#1124)', () => {
  it('keeps the default gap for small node counts', () => {
    expect(stepperTrackGap(1, 176)).toBe(STEPPER_MAX_GAP_PX)
    expect(stepperTrackGap(4, 176)).toBe(STEPPER_MAX_GAP_PX)
    expect(stepperTrackGap(30, 176)).toBe(STEPPER_MAX_GAP_PX)
  })

  it('shrinks the gap so segments stay visible at 50+ nodes', () => {
    // 50 段：4px gap 总量 196px 已超 176px track 宽，必须收缩。
    expect(stepperTrackGap(50, 176)).toBe(1)
    expect(stepperTrackGap(50, 200)).toBe(2)
  })

  it('never lets gap + minimum segment widths exceed the track width', () => {
    for (const width of [176, 200]) {
      for (let count = 2; count <= 200; count += 1) {
        const gap = stepperTrackGap(count, width)
        expect(gap).toBeGreaterThanOrEqual(0)
        expect(gap).toBeLessThanOrEqual(STEPPER_MAX_GAP_PX)
        const total = count * STEPPER_MIN_SEGMENT_PX + (count - 1) * gap
        if (count * STEPPER_MIN_SEGMENT_PX <= width) {
          expect(total).toBeLessThanOrEqual(width)
        } else {
          // 段数多到 2px 都放不下时 gap 归零，段宽交给 flex 均分收缩。
          expect(gap).toBe(0)
        }
      }
    }
  })
})
