import type { FeatureCollection, Polygon } from 'geojson'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { API_URL, ApiError, makeApi, type Bbox } from './api'
import { connectStream, type SseEvent, type StreamState } from './sse'
import { MIN_GRID_ZOOM } from './style'
import type { Alert, Health, Hotspot, HotspotDetail, NodeInfo, TrustProps } from './types'

type Grid = FeatureCollection<Polygon, TrustProps> & { sim: boolean }
export interface View {
  bbox: Bbox
  zoom: number
}

/** All server state for the dashboard. Optional pieces (nodes, health) fail quietly. */
export function useDashboard(key: string | null) {
  const api = useMemo(() => (key ? makeApi(key) : null), [key])
  const [role, setRole] = useState<string | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [hotspots, setHotspots] = useState<Hotspot[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [nodes, setNodes] = useState<NodeInfo[]>([])
  const [grid, setGrid] = useState<Grid | null>(null)
  const [gridError, setGridError] = useState<string | null>(null)
  const [stream, setStream] = useState<StreamState>('connecting')
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<HotspotDetail | null>(null)
  const [view, setView] = useState<View | null>(null)
  const [gridVersion, setGridVersion] = useState(0)
  const [lastLoad, setLastLoad] = useState<number | null>(null)

  const loadState = useCallback(async () => {
    if (!api) return
    const [h, hs, al, ns] = await Promise.allSettled([
      api.health(),
      api.hotspots(),
      api.alerts(),
      api.nodes(),
    ])
    if (h.status === 'fulfilled') setHealth(h.value)
    if (hs.status === 'fulfilled') setHotspots(hs.value.items)
    else setError(hs.reason instanceof Error ? hs.reason.message : 'cannot load hotspots')
    if (al.status === 'fulfilled') setAlerts(al.value.items)
    setNodes(ns.status === 'fulfilled' ? ns.value : []) // operators only; absent otherwise
    if (hs.status === 'fulfilled') {
      setError(null)
      setLastLoad(Date.now())
    }
  }, [api])

  useEffect(() => {
    if (!api || !key) return
    let cancelled = false
    api
      .me()
      .then((m) => !cancelled && setRole(m.role))
      .catch((e: unknown) => {
        if (!cancelled)
          setError(e instanceof ApiError && e.status === 401 ? 'invalid API key' : String(e))
      })
    const first = setTimeout(() => void loadState(), 0)
    const onEvent = (e: SseEvent) => {
      if (e.event === 'alert') {
        const a = JSON.parse(e.data) as Alert
        setAlerts((prev) => (prev.some((p) => p.id === a.id) ? prev : [a, ...prev].slice(0, 100)))
      } else if (e.event === 'refresh') {
        void loadState()
        setGridVersion((v) => v + 1)
      }
    }
    const close = connectStream({
      url: `${API_URL}/v1/stream/alerts`,
      key,
      onEvent,
      onState: setStream,
    })
    const poll = setInterval(() => void loadState(), 30000) // keeps health and ages honest
    return () => {
      cancelled = true
      clearTimeout(first)
      close()
      clearInterval(poll)
    }
  }, [api, key, loadState])

  const gridTooFar = view !== null && view.zoom < MIN_GRID_ZOOM
  useEffect(() => {
    if (!api || !view || gridTooFar) return
    const t = setTimeout(() => {
      api
        .trustGrid(view.bbox)
        .then((g) => {
          setGrid(g)
          setGridError(null)
        })
        .catch((e: unknown) =>
          setGridError(
            e instanceof ApiError && e.status === 422
              ? 'Area too large: zoom in'
              : 'Trust grid unavailable',
          ),
        )
    }, 300)
    return () => clearTimeout(t)
  }, [api, view, gridTooFar, gridVersion])
  const gridNote = gridTooFar ? `Zoom in to see the trust grid (zoom ${MIN_GRID_ZOOM}+)` : gridError

  useEffect(() => {
    if (!api || !selectedId) return
    let cancelled = false
    api
      .hotspot(selectedId)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setDetail(null))
    return () => {
      cancelled = true
    }
  }, [api, selectedId, gridVersion])

  return {
    role,
    health,
    hotspots,
    alerts,
    nodes,
    grid,
    gridNote,
    stream,
    error,
    lastLoad,
    selectedId,
    setSelectedId,
    detail,
    setView,
  }
}
