import { useEffect, useMemo, useState } from 'react'
import type { Bbox } from './api'
import { Detail } from './components/Detail'
import { HazardDetail } from './components/HazardDetail'
import { ReportPanel } from './components/ReportPanel'
import { KeyGate } from './components/KeyGate'
import { Legend } from './components/Legend'
import { MapView } from './components/MapView'
import { RoutePanel } from './components/RoutePanel'
import { Sidebar } from './components/Sidebar'
import { StatusBar } from './components/StatusBar'
import { bboxOf } from './geo'
import { hazardFeatures } from './hazards'
import { segmentFeatures, type Waypoint } from './route'
import type { Alert, Hazard, Hotspot, WatchedRouteItem } from './types'
import { usePlanner } from './usePlanner'
import { useDashboard } from './useDashboard'

const KEY_STORE = 'stozher.apiKey'

function initialKey(): string | null {
  try {
    const stored = sessionStorage.getItem(KEY_STORE)
    if (stored) return stored
  } catch {
    /* storage can be blocked */
  }
  // Dev server only: production builds never read this.
  return import.meta.env.DEV ? (import.meta.env.VITE_DEV_API_KEY ?? null) : null
}

export default function App() {
  const [key, setKey] = useState<string | null>(initialKey)
  const d = useDashboard(key)
  const planner = usePlanner(d.api, d.gridVersion)
  const [now, setNow] = useState(() => Date.now())
  const [placing, setPlacing] = useState(false)
  const [reportPoint, setReportPoint] = useState<Waypoint | null>(null)
  const [focus, setFocus] = useState<{ bbox: Bbox; n: number } | null>(null)

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15000)
    return () => clearInterval(t)
  }, [])

  const fly = (bbox: Bbox | null) => bbox && setFocus((f) => ({ bbox, n: (f?.n ?? 0) + 1 }))

  const routeFc = useMemo(() => segmentFeatures(planner.result), [planner.result])
  const watchedFc = useMemo(
    () => ({
      type: 'FeatureCollection' as const,
      features: d.watched.map((w) => ({
        type: 'Feature' as const,
        geometry: w.geometry,
        properties: { id: w.id, state: w.state ?? 'UNKNOWN' },
      })),
    }),
    [d.watched],
  )

  const hz = useMemo(() => hazardFeatures(d.hazards), [d.hazards])
  const canReport = d.role !== null && d.role !== 'public' && d.role !== 'system'
  const canDecide = d.role === 'operator' || d.role === 'admin'

  const hasSim = useMemo(
    () =>
      d.hotspots.some((h) => h.sim) ||
      d.hazards.some((h) => h.sim) ||
      d.alerts.some((a) => a.sim) ||
      !!d.grid?.sim ||
      !!planner.result?.sim ||
      d.nodes.some((n) => n.sim),
    [d.hotspots, d.hazards, d.alerts, d.grid, d.nodes, planner.result?.sim],
  )

  const pickHotspot = (h: Hotspot) => {
    d.setSelectedId(h.id)
    fly(bboxOf(h.source_ellipse))
  }
  const pickHazard = (h: Hazard) => {
    d.setSelectedHazardId(h.id)
    const b = bboxOf(h.geometry)
    if (b) fly([b[0] - 0.004, b[1] - 0.003, b[2] + 0.004, b[3] + 0.003])
  }
  const pickRoute = (w: WatchedRouteItem) => fly(bboxOf(w.geometry))
  const pickAlert = (a: Alert) => {
    if (a.hotspot_id) d.setSelectedId(a.hotspot_id)
    fly(bboxOf(a.area))
  }
  const logout = () => {
    try {
      sessionStorage.removeItem(KEY_STORE)
    } catch {
      /* ignore */
    }
    setKey(null)
  }

  if (!key || d.stream === 'denied' || d.error === 'invalid API key') {
    return (
      <KeyGate
        error={key ? 'That key was not accepted.' : null}
        onKey={(k) => {
          try {
            sessionStorage.setItem(KEY_STORE, k)
          } catch {
            /* ignore */
          }
          setKey(k)
        }}
      />
    )
  }

  return (
    <div className="app">
      <StatusBar
        stream={d.stream}
        role={d.role}
        health={d.health}
        hasSim={hasSim}
        lastLoad={d.lastLoad}
        now={now}
        onLogout={logout}
      />
      <Sidebar
        alerts={d.alerts}
        hotspots={d.hotspots}
        hazards={d.hazards}
        selectedHazardId={d.selectedHazard?.id ?? null}
        onPickHazard={pickHazard}
        watched={d.watched}
        selectedId={d.selectedId}
        now={now}
        onPickAlert={pickAlert}
        onPickHotspot={pickHotspot}
        onPickRoute={pickRoute}
        onDeleteRoute={(w) => void d.deleteWatch(w.id)}
      />
      <div className="mapwrap">
        <MapView
          hotspots={d.hotspots}
          nodes={d.nodes}
          grid={d.grid}
          selectedId={d.selectedId}
          routeFc={routeFc}
          watchedFc={watchedFc}
          hazardMarkers={hz.markers}
          hazardShapes={hz.shapes}
          selectedHazardId={d.selectedHazard?.id ?? null}
          onSelectHazard={d.setSelectedHazardId}
          placing={placing}
          reportPoint={reportPoint}
          onPlace={(p) => {
            setReportPoint(p)
            setPlacing(false)
          }}
          waypoints={planner.waypoints}
          drawing={planner.drawing}
          onAddWaypoint={planner.add}
          focus={focus}
          onSelect={d.setSelectedId}
          onView={d.setView}
        />
        {d.error && (
          <div className="banner error" role="alert">
            {d.error}
          </div>
        )}
        <RoutePanel
          waypoints={planner.waypoints.length}
          drawing={planner.drawing}
          result={planner.result}
          hazards={planner.hazardResult}
          hazardNote={planner.hazardNote}
          error={planner.error}
          bufferM={planner.bufferM}
          canWatch={d.role !== null && d.role !== 'public'}
          onBuffer={planner.setBufferM}
          onDrawing={(on) => {
            if (on) setPlacing(false)
            planner.setDrawing(on)
          }}
          onUndo={planner.undo}
          onClear={planner.clear}
          onWatch={async (w) => {
            await d.createWatch({ ...w, coordinates: planner.waypoints, bufferM: planner.bufferM })
          }}
        />
        {canReport && (
          <ReportPanel
            placing={placing}
            point={reportPoint}
            onPlacing={(on) => {
              if (on) planner.setDrawing(false)
              setPlacing(on)
            }}
            onClear={() => {
              setReportPoint(null)
              setPlacing(false)
            }}
            onSubmit={d.submitReport}
          />
        )}
        <Legend note={d.gridNote} />
        {hasSim && <div className="simbadge">SIMULATED DATA</div>}
        {d.selectedHazard && (
          <HazardDetail
            key={d.selectedHazard.id}
            h={d.selectedHazard}
            canDecide={canDecide}
            onDecide={async (a, r) => {
              await d.decide(d.selectedHazard!.id, a, r)
            }}
            onClose={() => d.setSelectedHazardId(null)}
          />
        )}
        {d.detail && !d.selectedHazard && (
          <Detail h={d.detail} now={now} onClose={() => d.setSelectedId(null)} />
        )}
      </div>
    </div>
  )
}
