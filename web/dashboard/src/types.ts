import type { Point, Polygon } from 'geojson'

export type TrustState = 'TRUSTED' | 'DEGRADED' | 'JAMMED' | 'SPOOFED' | 'UNKNOWN'
export type AlertLevel = 'info' | 'warning' | 'critical'

export interface Hotspot {
  id: string
  kind: 'jamming' | 'spoofing'
  state: 'active' | 'closed'
  centroid: Point
  source_ellipse: Polygon
  ellipse: { semi_major_m: number; semi_minor_m: number; bearing_deg: number }
  severity: number
  confidence: number
  first_seen: string
  last_seen: string
  event_count: number
  independent_sources: number
  sim: boolean
  data_age_s: number | null
}

export interface HotspotDetail extends Hotspot {
  timeline: { event_id: string; node_id: number; observed_at: string; confidence: number }[]
}

export interface Alert {
  id: number
  level: AlertLevel
  kind: string
  area: Polygon | null
  message: string
  hotspot_id: string | null
  created_at: string
  sim: boolean
}

export interface NodeInfo {
  id: number
  name: string
  kind: string
  status: string
  last_seen: string | null
  sim: boolean
  position: Point | null
}

export interface Health {
  status: string
  db: string
  modules: Record<string, 'present' | 'absent'>
}

export interface TrustProps {
  h3: string
  state: TrustState
  score: number | null
  confidence: number
  independent_sources: number
  data_age_s: number | null
  sim: boolean
}
