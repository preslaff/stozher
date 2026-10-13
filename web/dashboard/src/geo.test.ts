import { describe, expect, it } from 'vitest'
import { bboxOf, unionBbox } from './geo'

describe('bbox helpers', () => {
  it('computes the box of a polygon and a point', () => {
    const poly = {
      type: 'Polygon' as const,
      coordinates: [
        [
          [1, 2],
          [3, 2],
          [3, 5],
          [1, 2],
        ],
      ],
    }
    expect(bboxOf(poly)).toEqual([1, 2, 3, 5])
    expect(bboxOf({ type: 'Point', coordinates: [7, 8] })).toEqual([7, 8, 7, 8])
    expect(bboxOf(null)).toBeNull()
  })

  it('unions boxes and ignores nulls', () => {
    expect(unionBbox([[0, 0, 1, 1], null, [2, -1, 3, 0.5]])).toEqual([0, -1, 3, 1])
    expect(unionBbox([null])).toBeNull()
  })
})
