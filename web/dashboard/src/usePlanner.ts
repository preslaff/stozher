import { useCallback, useEffect, useState } from 'react'
import { ApiError, type Api } from './api'
import type { Waypoint } from './route'
import type { HazardRoute, RouteTrust } from './types'

const MAX_WAYPOINTS = 200

/** The route being planned: waypoints clicked on the map, and its trust from the API. */
export function usePlanner(api: Api | null, refresh: number) {
  const [waypoints, setWaypoints] = useState<Waypoint[]>([])
  const [drawing, setDrawing] = useState(false)
  const [bufferM, setBufferM] = useState(300)
  const [result, setResult] = useState<RouteTrust | null>(null)
  const [hazardResult, setHazardResult] = useState<HazardRoute | null>(null)
  const [hazardNote, setHazardNote] = useState<string | null>(null)
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
      // hazards are a separate result and are never merged into the GNSS trust verdict
      api
        .hazardsAlongRoute(waypoints, bufferM)
        .then((r) => {
          if (cancelled) return
          setHazardResult(r)
          setHazardNote(null)
        })
        .catch((e: unknown) => {
          if (cancelled) return
          setHazardResult(null)
          setHazardNote(
            e instanceof ApiError && e.status === 403
              ? 'The hazard check needs a responder key.'
              : 'Hazard check unavailable.',
          )
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
    setHazardResult(null)
    setHazardNote(null)
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
    hazardResult: waypoints.length >= 2 ? hazardResult : null,
    hazardNote: waypoints.length >= 2 ? hazardNote : null,
    error: waypoints.length >= 2 ? error : null,
    add,
    undo,
    clear,
  }
}
