import type { FeatureCollection, Polygon } from 'geojson'
import type {
  Alert,
  Hazard,
  HazardKind,
  HazardRoute,
  HazardTier,
  Health,
  Hotspot,
  HotspotDetail,
  MinLevel,
  NodeInfo,
  Passability,
  RebindResult,
  RouteTrust,
  TrustProps,
  UnknownSender,
  WatchedRouteItem,
} from './types'

export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export interface ListResponse<T> {
  items: T[]
  count: number
  data_age_s?: number | null
}

export type Bbox = [number, number, number, number] // minLon, minLat, maxLon, maxLat

export function makeApi(key: string, base: string = API_URL) {
  async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { 'X-API-Key': key }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const r = await fetch(base + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!r.ok) {
      let detail = r.statusText
      try {
        detail = ((await r.json()) as { detail?: string }).detail ?? detail
      } catch {
        /* not a problem document */
      }
      throw new ApiError(r.status, detail)
    }
    return (r.status === 204 ? undefined : await r.json()) as T
  }
  const get = <T>(path: string) => send<T>('GET', path)
  return {
    health: () => get<Health>('/health'),
    me: () => get<{ role: string; label: string }>('/v1/me'),
    hotspots: () => get<ListResponse<Hotspot>>('/v1/hotspots?limit=200'),
    hotspot: (id: string) => get<HotspotDetail>(`/v1/hotspots/${id}`),
    alerts: () => get<ListResponse<Alert>>('/v1/alerts?limit=50'),
    nodes: () => get<NodeInfo[]>('/v1/nodes'),
    unknownSenders: () => get<UnknownSender[]>('/v1/nodes/unrecognised-senders?transport=mesh'),
    rebind: (nodeId: number, transport: string, externalId: string) =>
      send<RebindResult>('POST', `/v1/nodes/${nodeId}/transport-ids/rebind`, {
        transport,
        external_id: externalId,
      }),
    trustRoute: (coordinates: number[][], bufferM: number) =>
      send<RouteTrust>('POST', `/v1/trust/route?buffer_m=${bufferM}`, {
        type: 'LineString',
        coordinates,
      }),
    watchedRoutes: () => get<ListResponse<WatchedRouteItem>>('/v1/watched-routes'),
    createWatch: (w: {
      name: string
      coordinates: number[][]
      bufferM: number
      minLevel: MinLevel
      hazardMinTier: HazardTier
      hours: number
    }) =>
      send<{ id: string }>('POST', '/v1/watched-routes', {
        name: w.name,
        geometry: { type: 'LineString', coordinates: w.coordinates },
        buffer_m: w.bufferM,
        min_level: w.minLevel,
        hazard_min_tier: w.hazardMinTier,
        expires_at: new Date(Date.now() + w.hours * 3600_000).toISOString(),
      }),
    deleteWatch: (id: string) => send<void>('DELETE', `/v1/watched-routes/${id}`),
    hazards: () => get<ListResponse<Hazard>>('/v1/hazards?limit=500'),
    hazardsAlongRoute: (coordinates: number[][], bufferM: number) =>
      send<HazardRoute>('POST', `/v1/hazards/along-route?buffer_m=${bufferM}`, {
        type: 'LineString',
        coordinates,
      }),
    /** The four fields of a report and nothing else: a place, a kind, a passability and a time. */
    report: (r: {
      kind: HazardKind
      passability: Passability
      lon: number
      lat: number
      observedAt: Date
    }) =>
      send<{ id: string; tier: HazardTier }>('POST', '/v1/reports', {
        kind: r.kind,
        passability: r.passability,
        geometry: { type: 'Point', coordinates: [r.lon, r.lat] },
        observed_at: r.observedAt.toISOString(),
      }),
    decide: (id: string, action: 'confirm' | 'close' | 'dismiss', reason: string) =>
      send<Hazard>('POST', `/v1/hazards/${id}/${action}`, { reason }),
    trustGrid: (b: Bbox) =>
      get<FeatureCollection<Polygon, TrustProps> & { sim: boolean }>(
        `/v1/trust/grid?bbox=${b.map((n) => n.toFixed(5)).join(',')}`,
      ),
  }
}

export type Api = ReturnType<typeof makeApi>
