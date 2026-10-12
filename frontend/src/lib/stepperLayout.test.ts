import { describe, expect, it } from 'vitest'
import {
  STEPPER_COMPACT_THRESHOLD,
  STEPPER_MAX_GAP_PX,
  STEPPER_MIN_SEGMENT_PX,
  isStepperCompact,
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

describe('isStepperCompact / 紧凑形态阈值 (#1257)', () => {
  it('keeps the segmented form at and below the default threshold (49/50)', () => {
    expect(STEPPER_COMPACT_THRESHOLD).toBe(50)
    expect(isStepperCompact(49)).toBe(false)
    expect(isStepperCompact(50)).toBe(false)
  })

  it('switches to compact above the default threshold (51+)', () => {
    expect(isStepperCompact(51)).toBe(true)
    expect(isStepperCompact(100)).toBe(true)
    expect(isStepperCompact(200)).toBe(true)
  })

  it('honours a custom threshold', () => {
    expect(isStepperCompact(10, 10)).toBe(false)
    expect(isStepperCompact(11, 10)).toBe(true)
    expect(isStepperCompact(2, 1)).toBe(true)
  })
})
