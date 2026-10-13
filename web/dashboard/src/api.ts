import type { FeatureCollection, Polygon } from 'geojson'
import type {
  Alert,
  Health,
  Hotspot,
  HotspotDetail,
  MinLevel,
  NodeInfo,
  RouteTrust,
  TrustProps,
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
      hours: number
    }) =>
      send<{ id: string }>('POST', '/v1/watched-routes', {
        name: w.name,
        geometry: { type: 'LineString', coordinates: w.coordinates },
        buffer_m: w.bufferM,
        min_level: w.minLevel,
        expires_at: new Date(Date.now() + w.hours * 3600_000).toISOString(),
      }),
    deleteWatch: (id: string) => send<void>('DELETE', `/v1/watched-routes/${id}`),
    trustGrid: (b: Bbox) =>
      get<FeatureCollection<Polygon, TrustProps> & { sim: boolean }>(
        `/v1/trust/grid?bbox=${b.map((n) => n.toFixed(5)).join(',')}`,
      ),
  }
}

export type Api = ReturnType<typeof makeApi>
