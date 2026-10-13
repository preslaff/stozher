import { useCallback, useEffect, useState } from 'react'
import type { Api } from './api'
import type { Waypoint } from './route'
import type { RouteTrust } from './types'

const MAX_WAYPOINTS = 200

/** The route being planned: waypoints clicked on the map, and its trust from the API. */
export function usePlanner(api: Api | null, refresh: number) {
  const [waypoints, setWaypoints] = useState<Waypoint[]>([])
  const [drawing, setDrawing] = useState(false)
  const [bufferM, setBufferM] = useState(300)
  const [result, setResult] = useState<RouteTrust | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!api || waypoints.length < 2) return
    let cancelled = false
    const t = setTimeout(() => {
      api
        .trustRoute(waypoints, bufferM)
        .then((r) => {
          if (cancelled) return
          setResult(r)
          setError(null)
        })
        .catch((e: unknown) => {
          if (!cancelled) setError(e instanceof Error ? e.message : 'route check failed')
        })
    }, 400)
    return () => {
      cancelled = true
      clearTimeout(t)
    }
  }, [api, waypoints, bufferM, refresh]) // refresh: the grid changed, so check the route again

  const add = useCallback(
    (p: Waypoint) => setWaypoints((w) => (w.length < MAX_WAYPOINTS ? [...w, p] : w)),
    [],
  )
  const undo = useCallback(() => setWaypoints((w) => w.slice(0, -1)), [])
  const clear = useCallback(() => {
    setWaypoints([])
    setResult(null)
    setError(null)
    setDrawing(false)
  }, [])

  return {
    waypoints,
    drawing,
    setDrawing,
    bufferM,
    setBufferM,
    result: waypoints.length >= 2 ? result : null,
    error: waypoints.length >= 2 ? error : null,
    add,
    undo,
    clear,
  }
}
