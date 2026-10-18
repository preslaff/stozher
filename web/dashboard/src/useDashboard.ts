import type { FeatureCollection, Polygon } from 'geojson'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { API_URL, ApiError, makeApi, type Bbox } from './api'
import { connectStream, type SseEvent, type StreamState } from './sse'
import { MIN_GRID_ZOOM, quietNote } from './style'
import type {
  Alert,
  Hazard,
  Health,
  Hotspot,
  HistoryProps,
  HistoryWindow,
  HotspotDetail,
  NodeInfo,
  TrustProps,
  UnknownSender,
  WatchedRouteItem,
} from './types'

type Grid = FeatureCollection<Polygon, TrustProps> & {
  sim: boolean
  newest_evidence_age_s?: number | null
}
type History = FeatureCollection<Polygon, HistoryProps> & { sim: boolean }
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
  const [senders, setSenders] = useState<UnknownSender[]>([])
  const [watched, setWatched] = useState<WatchedRouteItem[]>([])
  const [hazards, setHazards] = useState<Hazard[]>([])
  const [selectedHazardId, setSelectedHazardId] = useState<string | null>(null)
  const [grid, setGrid] = useState<Grid | null>(null)
  const [gridError, setGridError] = useState<string | null>(null)
  const [history, setHistory] = useState<History | null>(null)
  const [historyOn, setHistoryOn] = useState(false)
  const [historyWindow, setHistoryWindow] = useState<HistoryWindow>('24h')
  const [stream, setStream] = useState<StreamState>('connecting')
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<HotspotDetail | null>(null)
  const [view, setView] = useState<View | null>(null)
  const [gridVersion, setGridVersion] = useState(0)
  const [lastLoad, setLastLoad] = useState<number | null>(null)

  const loadState = useCallback(async () => {
    if (!api) return
    const [h, hs, al, ns, wr, hz, us] = await Promise.allSettled([
      api.health(),
      api.hotspots(),
      api.alerts(),
      api.nodes(),
      api.watchedRoutes(),
      api.hazards(),
      api.unknownSenders(),
    ])
    if (h.status === 'fulfilled') setHealth(h.value)
    if (hs.status === 'fulfilled') setHotspots(hs.value.items)
    else setError(hs.reason instanceof Error ? hs.reason.message : 'cannot load hotspots')
    if (al.status === 'fulfilled') setAlerts(al.value.items)
    setNodes(ns.status === 'fulfilled' ? ns.value : []) // operators only; absent otherwise
    setSenders(us.status === 'fulfilled' ? us.value : []) // operators only; absent otherwise
    setWatched(wr.status === 'fulfilled' ? wr.value.items : []) // responders and above
    setHazards(hz.status === 'fulfilled' ? hz.value.items : []) // optional module: absent is fine
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
        if (a.kind.startsWith('route_hazard')) void loadState() // hazards changed
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
  // Why an empty grid is empty. Only once a grid has actually loaded for the current view.
  const quiet =
    gridTooFar || gridError
      ? null
      : grid
        ? quietNote(grid.features.length, grid.newest_evidence_age_s)
        : null

  // Expired history: responders and above only (the API refuses the rest), and only when the user switches it on.
  const canHistory = role !== null && role !== 'public' && role !== 'reporter' && role !== 'system'
  useEffect(() => {
    if (!api || !view || gridTooFar || !historyOn || !canHistory) return
    const t = setTimeout(() => {
      api
        .trustHistory(view.bbox, historyWindow)
        .then(setHistory)
        .catch(() => setHistory(null))
    }, 300)
    return () => clearTimeout(t)
  }, [api, view, gridTooFar, historyOn, canHistory, historyWindow, gridVersion])
  const shownHistory = historyOn && canHistory && !gridTooFar ? history : null

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

  const createWatch = useCallback(
    async (w: Parameters<NonNullable<typeof api>['createWatch']>[0]) => {
      if (!api) return
      await api.createWatch(w)
      await loadState()
    },
    [api, loadState],
  )
  const deleteWatch = useCallback(
    async (id: string) => {
      if (!api) return
      await api.deleteWatch(id)
      await loadState()
    },
    [api, loadState],
  )

  const decide = useCallback(
    async (id: string, action: 'confirm' | 'close' | 'dismiss', reason: string) => {
      if (!api) return
      await api.decide(id, action, reason)
      await loadState()
      setGridVersion((v) => v + 1) // route checks run again
    },
    [api, loadState],
  )
  const bindSender = useCallback(
    async (s: UnknownSender) => {
      if (!api || s.candidate_node_id === null) return null
      const result = await api.rebind(s.candidate_node_id, s.transport, s.external_id)
      await loadState()
      return result
    },
    [api, loadState],
  )
  const submitReport = useCallback(
    async (r: Parameters<NonNullable<typeof api>['report']>[0]) => {
      if (!api) return
      await api.report(r)
      // the worker merges it into a hazard within seconds; look again shortly
      setTimeout(() => void loadState(), 3000)
    },
    [api, loadState],
  )
  const selectedHazard = hazards.find((x) => x.id === selectedHazardId) ?? null

  return {
    api,
    hazards,
    selectedHazard,
    setSelectedHazardId,
    decide,
    senders,
    bindSender,
    submitReport,
    gridVersion,
    watched,
    createWatch,
    deleteWatch,
    role,
    health,
    hotspots,
    alerts,
    nodes,
    grid,
    gridNote,
    quiet,
    history: shownHistory,
    historyOn,
    setHistoryOn,
    historyWindow,
    setHistoryWindow,
    canHistory,
    stream,
    error,
    lastLoad,
    selectedId,
    setSelectedId,
    detail,
    setView,
  }
}
