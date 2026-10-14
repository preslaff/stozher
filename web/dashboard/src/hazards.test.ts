import { describe, expect, it } from 'vitest'
import {
  DECISIONS,
  KIND_LABEL,
  REPORT_KINDS,
  hazardFeatures,
  observedAt,
  summary,
  tierCounts,
} from './hazards'
import type { Hazard } from './types'

const base: Hazard = {
  id: 'a',
  kind: 'flood',
  passability: 'blocked',
  tier: 'unverified',
  status: 'active',
  geometry: { type: 'Point', coordinates: [27.9, 43.2] },
  valid_until: '2026-11-28T00:00:00Z',
  sim: false,
  data_age_s: 60,
}

describe('hazard helpers', () => {
  it('offers only physical road conditions and never the official-only kind', () => {
    const kinds = REPORT_KINDS.map((k) => k.value)
    expect(kinds).toHaveLength(7)
    expect(kinds).not.toContain('closed_by_authority')
    expect(KIND_LABEL.closed_by_authority).toBeDefined()
  })

  it('never puts a report in the future', () => {
    const now = Date.parse('2026-11-28T10:00:00Z')
    expect(observedAt(0, now).getTime()).toBe(now)
    expect(observedAt(15, now).toISOString()).toBe('2026-11-28T09:45:00.000Z')
  })

  it('summarises with the tier, so an unverified hazard is never shown as fact', () => {
    expect(summary(base)).toBe('Flooded road · blocked · unverified')
  })

  it('builds a marker for every active hazard and an outline only for shapes', () => {
    const hull: Hazard = {
      ...base,
      id: 'b',
      tier: 'corroborated',
      geometry: {
        type: 'Polygon',
        coordinates: [
          [
            [27.9, 43.2],
            [27.902, 43.2],
            [27.902, 43.202],
            [27.9, 43.2],
          ],
        ],
      },
    }
    const ended: Hazard = { ...base, id: 'c', status: 'dismissed' }
    const { markers, shapes } = hazardFeatures([base, hull, ended])
    expect(markers.features.map((f) => f.properties?.id)).toEqual(['a', 'b'])
    expect(shapes.features.map((f) => f.properties?.id)).toEqual(['b'])
    expect(markers.features[1].geometry.coordinates).toEqual([27.901, 43.201])
  })

  it('counts only active hazards by tier', () => {
    const c = tierCounts([base, { ...base, tier: 'confirmed' }, { ...base, status: 'expired' }])
    expect(c).toEqual({ unverified: 1, corroborated: 0, confirmed: 1 })
  })

  it('keeps decision reasons to the closed lists', () => {
    expect(DECISIONS.dismiss).toContain('false_report')
    expect(DECISIONS.confirm).not.toContain('false_report')
  })
})
