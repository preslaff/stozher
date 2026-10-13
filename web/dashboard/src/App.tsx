import { useEffect, useMemo, useState } from 'react'
import type { Bbox } from './api'
import { Detail } from './components/Detail'
import { KeyGate } from './components/KeyGate'
import { Legend } from './components/Legend'
import { MapView } from './components/MapView'
import { Sidebar } from './components/Sidebar'
import { StatusBar } from './components/StatusBar'
import { bboxOf } from './geo'
import type { Alert, Hotspot } from './types'
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
  const [now, setNow] = useState(() => Date.now())
  const [focus, setFocus] = useState<{ bbox: Bbox; n: number } | null>(null)

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15000)
    return () => clearInterval(t)
  }, [])

  const fly = (bbox: Bbox | null) => bbox && setFocus((f) => ({ bbox, n: (f?.n ?? 0) + 1 }))

  const hasSim = useMemo(
    () =>
      d.hotspots.some((h) => h.sim) ||
      d.alerts.some((a) => a.sim) ||
      !!d.grid?.sim ||
      d.nodes.some((n) => n.sim),
    [d.hotspots, d.alerts, d.grid, d.nodes],
  )

  const pickHotspot = (h: Hotspot) => {
    d.setSelectedId(h.id)
    fly(bboxOf(h.source_ellipse))
  }
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
        selectedId={d.selectedId}
        now={now}
        onPickAlert={pickAlert}
        onPickHotspot={pickHotspot}
      />
      <div className="mapwrap">
        <MapView
          hotspots={d.hotspots}
          nodes={d.nodes}
          grid={d.grid}
          selectedId={d.selectedId}
          focus={focus}
          onSelect={d.setSelectedId}
          onView={d.setView}
        />
        {d.error && (
          <div className="banner error" role="alert">
            {d.error}
          </div>
        )}
        <Legend note={d.gridNote} />
        {hasSim && <div className="simbadge">SIMULATED DATA</div>}
        {d.detail && <Detail h={d.detail} now={now} onClose={() => d.setSelectedId(null)} />}
      </div>
    </div>
  )
}
