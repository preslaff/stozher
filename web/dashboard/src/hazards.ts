import type { Feature, FeatureCollection, Geometry, Point, Position } from 'geojson'
import { bboxOf } from './geo'
import type { Hazard, HazardKind, HazardTier, Passability } from './types'

/** The closed list a reporter may choose from: physical road conditions only. */
export const REPORT_KINDS: { value: Exclude<HazardKind, 'closed_by_authority'>; label: string }[] =
  [
    { value: 'flood', label: 'Flooded road' },
    { value: 'landslide', label: 'Landslide' },
    { value: 'debris', label: 'Debris on the road' },
    { value: 'road_collapse', label: 'Road collapsed' },
    { value: 'bridge_damage', label: 'Bridge damaged' },
    { value: 'fire_near_road', label: 'Fire near the road' },
    { value: 'snow_ice', label: 'Snow or ice' },
  ]

export const KIND_LABEL: Record<HazardKind, string> = {
  ...Object.fromEntries(REPORT_KINDS.map((k) => [k.value, k.label])),
  closed_by_authority: 'Closed by authority',
} as Record<HazardKind, string>

export const PASSABILITY: { value: Passability; label: string }[] = [
  { value: 'blocked', label: 'Blocked' },
  { value: 'restricted', label: 'Restricted' },
  { value: 'passable_with_care', label: 'Passable with care' },
  { value: 'reported_clear', label: 'Reported clear' },
]

export const PASSABILITY_LABEL = Object.fromEntries(
  PASSABILITY.map((p) => [p.value, p.label]),
) as Record<Passability, string>

/** Tier colours differ from the GNSS trust colours on purpose: they are a different result. */
export const TIER_COLOR: Record<HazardTier, string> = {
  unverified: '#b8c0cc',
  corroborated: '#ff8c42',
  confirmed: '#ff3d81',
}

export const TIER_TEXT: Record<HazardTier, string> = {
  unverified: 'Unverified: one source, not confirmed',
  corroborated: 'Corroborated: independent reports',
  confirmed: 'Confirmed by an operator',
}

/** Reports offered for "when did you see it": never in the future, never older than the server allows. */
export const WHEN_OPTIONS: { minutes: number; label: string }[] = [
  { minutes: 0, label: 'Just now' },
  { minutes: 15, label: '15 minutes ago' },
  { minutes: 60, label: '1 hour ago' },
  { minutes: 180, label: '3 hours ago' },
]

export function observedAt(minutesAgo: number, now: number = Date.now()): Date {
  return new Date(now - minutesAgo * 60_000)
}

/** The decisions an operator can take, each with a reason from the server's closed list. */
export const DECISIONS = {
  confirm: ['verified_on_site', 'official_source', 'multiple_sources'],
  close: ['cleared_on_site', 'official_source', 'no_longer_applicable'],
  dismiss: ['false_report', 'mistaken_location', 'duplicate', 'test_data'],
} as const

export function reasonLabel(r: string): string {
  const t = r.replace(/_/g, ' ')
  return t.charAt(0).toUpperCase() + t.slice(1)
}

/** Centre of a hazard's box, for the marker. */
export function centre(g: Geometry | null): Position | null {
  const b = bboxOf(g)
  return b ? [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] : null
}

/** One marker per hazard (carrying tier and kind) plus the outline for shapes that are not points. */
export function hazardFeatures(hs: Hazard[]): {
  markers: FeatureCollection<Point>
  shapes: FeatureCollection
} {
  const markers: Feature<Point>[] = []
  const shapes: Feature[] = []
  for (const h of hs) {
    const c = centre(h.geometry)
    if (!c || h.status !== 'active') continue
    const props = { id: h.id, tier: h.tier, kind: h.kind, passability: h.passability }
    markers.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: c },
      properties: props,
    })
    if (h.geometry && h.geometry.type !== 'Point')
      shapes.push({ type: 'Feature', geometry: h.geometry, properties: props })
  }
  return {
    markers: { type: 'FeatureCollection', features: markers },
    shapes: { type: 'FeatureCollection', features: shapes },
  }
}

/** The honest one-line summary: tier and age are always part of it, never a bare "blocked". */
export function summary(h: Pick<Hazard, 'kind' | 'passability' | 'tier'>): string {
  return `${KIND_LABEL[h.kind]} · ${PASSABILITY_LABEL[h.passability].toLowerCase()} · ${h.tier}`
}

export function tierCounts(hs: Hazard[]): Record<HazardTier, number> {
  const out: Record<HazardTier, number> = { unverified: 0, corroborated: 0, confirmed: 0 }
  for (const h of hs) if (h.status === 'active') out[h.tier]++
  return out
}
