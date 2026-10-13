import type { FeatureCollection, Polygon } from 'geojson'
import type { Alert, Health, Hotspot, HotspotDetail, NodeInfo, TrustProps } from './types'

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
  async function get<T>(path: string): Promise<T> {
    const r = await fetch(base + path, { headers: { 'X-API-Key': key } })
    if (!r.ok) {
      let detail = r.statusText
      try {
        detail = ((await r.json()) as { detail?: string }).detail ?? detail
      } catch {
        /* not a problem document */
      }
      throw new ApiError(r.status, detail)
    }
    return (await r.json()) as T
  }
  return {
    health: () => get<Health>('/health'),
    me: () => get<{ role: string; label: string }>('/v1/me'),
    hotspots: () => get<ListResponse<Hotspot>>('/v1/hotspots?limit=200'),
    hotspot: (id: string) => get<HotspotDetail>(`/v1/hotspots/${id}`),
    alerts: () => get<ListResponse<Alert>>('/v1/alerts?limit=50'),
    nodes: () => get<NodeInfo[]>('/v1/nodes'),
    trustGrid: (b: Bbox) =>
      get<FeatureCollection<Polygon, TrustProps> & { sim: boolean }>(
        `/v1/trust/grid?bbox=${b.map((n) => n.toFixed(5)).join(',')}`,
      ),
  }
}

export type Api = ReturnType<typeof makeApi>
