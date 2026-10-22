import type { Geometry, LineString, Point, Polygon } from 'geojson'

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
  area: Polygon | LineString | null
  message: string
  hotspot_id: string | null
  route_id: string | null
  created_at: string
  sim: boolean
}

/** What the cloud holds of a node's records and whether the node was told (GET /v1/nodes, operators; docs/control.md). */
export interface Delivery {
  key_id: number
  records: number
  highest_seq: number
  contiguous_seq: number
  missing: number
  last_ack_at: string | null
  ack_current: boolean | null
  /** highest sequence the newest ack covers; null if never acknowledged */
  acked_through?: number | null
  /** records held that the newest ack does not cover */
  unacked?: number
}

export interface NodeInfo {
  id: number
  name: string
  kind: string
  status: string
  last_seen: string | null
  sim: boolean
  position: Point | null
  delivery?: Delivery | null
}

/** A radio ID that sent something we could not place, for example after a reflash (docs/mesh.md). */
export interface UnknownSender {
  transport: string
  external_id: string
  first_seen: string
  last_seen: string
  count: number
  candidate_node_id: number | null
  candidate_name: string | null
}

export interface RebindResult {
  node: NodeInfo
  recovered: { status: string; reason: string | null }[]
}

/** POST /v1/assistant/query (docs/assistant.md). The labels (age, sources, SIM, caveats) are computed by the server, not by the model. */
export interface AssistantAnswer {
  answer: string
  engine: string
  model: string
  grounded: boolean
  data_age_s: number | null
  sim: boolean
  sources: string[]
  caveats: string[]
  tools: { name: string; data_age_s: number | null; expired: boolean; error: boolean }[]
}

export interface Health {
  status: string
  db: string
  modules: Record<string, 'present' | 'absent'>
  /** One entry per gateway radio heard in the last day (`main`, or a name such as `433`); older APIs omit it. */
  mesh_bridges?: Record<string, 'present' | 'absent'>
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

/** One cell of GET /v1/trust/history: what a place looked like at its newest evidence. Expired: never a current state. */
export interface HistoryProps {
  h3: string
  expired: true
  last_state: TrustState
  last_score: number | null
  newest_observed_at: string
  data_age_s: number
  event_count: number
  independent_sources: number
  sim: boolean
}

export type HistoryWindow = '6h' | '24h' | '7d'

export type MinLevel = 'degraded' | 'jammed' | 'spoofed'

export interface RouteSegment {
  index: number
  start_m: number
  end_m: number
  geometry: LineString
  state: TrustState
  score: number | null
  confidence: number
  data_age_s: number | null
  independent_sources: number
  sim: boolean
}

export interface RouteTrust {
  length_m: number
  overall: {
    state: TrustState
    score: number | null
    confidence: number
    data_age_s: number | null
    independent_sources: number
    worst_segment: number
  }
  segments: RouteSegment[]
  sim: boolean
  data_age_s: number | null
}

export interface WatchedRouteItem {
  id: string
  name: string
  buffer_m: number
  min_level: MinLevel
  state: TrustState | null
  created_at: string
  expires_at: string
  geometry: LineString
}

export type HazardTier = 'unverified' | 'corroborated' | 'confirmed'
export type HazardKind =
  | 'flood'
  | 'landslide'
  | 'debris'
  | 'road_collapse'
  | 'bridge_damage'
  | 'fire_near_road'
  | 'snow_ice'
  | 'closed_by_authority'
export type Passability = 'blocked' | 'restricted' | 'passable_with_care' | 'reported_clear'

/** A road hazard. Public and reporter callers get the coarse view: no sources, evidence or decisions. */
export interface Hazard {
  id: string
  kind: HazardKind
  passability: Passability
  tier: HazardTier
  status: 'active' | 'closed' | 'expired' | 'dismissed'
  geometry: Geometry | null
  valid_until: string
  sim: boolean
  data_age_s: number | null
  blur_m?: number
  sources?: string[]
  evidence?: { reports: number; clear_reports: number; independent_teams: number }
  decision?: string | null
  decision_reason?: string | null
  last_confirmed?: string | null
}

export interface RouteHazard extends Hazard {
  at_m: number
  distance_m: number
}

export interface HazardRoute {
  items: RouteHazard[]
  count: number
  length_m: number | null
  data_age_s: number | null
}
