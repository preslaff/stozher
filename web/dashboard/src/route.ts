import type { Feature, FeatureCollection, LineString } from 'geojson'
import type { RouteSegment, RouteTrust, TrustState } from './types'

export type Waypoint = [number, number] // lon, lat

export function kmText(m: number): string {
  return m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`
}

export function rangeText(s: Pick<RouteSegment, 'start_m' | 'end_m'>): string {
  return `${(s.start_m / 1000).toFixed(1)}–${(s.end_m / 1000).toFixed(1)} km`
}

/** Segments as a FeatureCollection for the map, each carrying its trust state. */
export function segmentFeatures(r: RouteTrust | null): FeatureCollection<LineString> {
  const features: Feature<LineString>[] = (r?.segments ?? []).map((s) => ({
    type: 'Feature',
    geometry: s.geometry,
    properties: { index: s.index, state: s.state },
  }))
  return { type: 'FeatureCollection', features }
}

const VERDICT: Record<TrustState, string> = {
  TRUSTED: 'GNSS looks trustworthy along this route',
  DEGRADED: 'GNSS is degraded on part of this route',
  JAMMED: 'GNSS interference likely on part of this route',
  SPOOFED: 'GNSS spoofing likely on part of this route',
  UNKNOWN: 'No recent evidence along part of this route',
}

export function verdict(state: TrustState): string {
  return VERDICT[state]
}
