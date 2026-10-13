import { describe, expect, it } from 'vitest'
import { kmText, rangeText, segmentFeatures, verdict } from './route'
import type { RouteTrust } from './types'

const trust: RouteTrust = {
  length_m: 9000,
  overall: {
    state: 'JAMMED',
    score: 8,
    confidence: 0.9,
    data_age_s: 60,
    independent_sources: 3,
    worst_segment: 1,
  },
  sim: false,
  data_age_s: 60,
  segments: [seg(0, 0, 4000, 'UNKNOWN'), seg(1, 4000, 6200, 'JAMMED')],
}

function seg(index: number, a: number, b: number, state: RouteTrust['segments'][0]['state']) {
  return {
    index,
    start_m: a,
    end_m: b,
    geometry: {
      type: 'LineString' as const,
      coordinates: [
        [27.9, 43.2],
        [27.95, 43.2],
      ],
    },
    state,
    score: null,
    confidence: 0,
    data_age_s: null,
    independent_sources: 0,
    sim: false,
  }
}

describe('route helpers', () => {
  it('formats distances', () => {
    expect(kmText(450)).toBe('450 m')
    expect(kmText(9040)).toBe('9.0 km')
    expect(rangeText({ start_m: 4000, end_m: 6200 })).toBe('4.0–6.2 km')
  })

  it('turns segments into features that keep their state', () => {
    const fc = segmentFeatures(trust)
    expect(fc.features.map((f) => f.properties?.state)).toEqual(['UNKNOWN', 'JAMMED'])
    expect(segmentFeatures(null).features).toEqual([])
  })

  it('has a plain-language verdict for every state', () => {
    for (const s of ['TRUSTED', 'DEGRADED', 'JAMMED', 'SPOOFED', 'UNKNOWN'] as const)
      expect(verdict(s).length).toBeGreaterThan(10)
  })
})
