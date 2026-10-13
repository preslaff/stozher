import * as maplibregl from 'maplibre-gl'
import type { GeoJSONSource, StyleSpecification } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useEffect, useRef, useState } from 'react'
import type { Bbox } from '../api'
import type { View } from '../useDashboard'
import { TRUST_COLOR } from '../style'
import { bboxOf, unionBbox } from '../geo'
import type { Hotspot, NodeInfo, TrustProps } from '../types'
import type { FeatureCollection, Polygon } from 'geojson'

const TILE_URL: string =
  import.meta.env.VITE_TILE_URL ?? 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'

const STYLE: StyleSpecification = {
  version: 8,
  sources: {
    base: {
      type: 'raster',
      tiles: [TILE_URL],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [
    { id: 'bg', type: 'background', paint: { 'background-color': '#0f151b' } },
    {
      id: 'base',
      type: 'raster',
      source: 'base',
      paint: { 'raster-opacity': 0.65, 'raster-saturation': -0.7, 'raster-brightness-max': 0.65 },
    },
  ],
}

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }
const stateColor: maplibregl.ExpressionSpecification = [
  'match',
  ['get', 'state'],
  'TRUSTED',
  TRUST_COLOR.TRUSTED,
  'DEGRADED',
  TRUST_COLOR.DEGRADED,
  'JAMMED',
  TRUST_COLOR.JAMMED,
  'SPOOFED',
  TRUST_COLOR.SPOOFED,
  TRUST_COLOR.UNKNOWN,
]
const kindColor: maplibregl.ExpressionSpecification = [
  'match',
  ['get', 'kind'],
  'spoofing',
  TRUST_COLOR.SPOOFED,
  TRUST_COLOR.JAMMED,
]

interface Props {
  hotspots: Hotspot[]
  nodes: NodeInfo[]
  grid: FeatureCollection<Polygon, TrustProps> | null
  selectedId: string | null
  focus: { bbox: Bbox; n: number } | null
  onSelect: (id: string | null) => void
  onView: (v: View) => void
}

export function MapView({ hotspots, nodes, grid, selectedId, focus, onSelect, onView }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const [ready, setReady] = useState(false)
  const cb = useRef({ onSelect, onView })
  useEffect(() => {
    cb.current = { onSelect, onView }
  })
  const fitted = useRef(false)

  useEffect(() => {
    if (!box.current) return
    const map = new maplibregl.Map({
      container: box.current,
      style: STYLE,
      center: [27.95, 43.2],
      zoom: 10,
      attributionControl: { compact: true },
    })
    mapRef.current = map
    if (import.meta.env.DEV) (window as unknown as { __map: maplibregl.Map }).__map = map
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
    const emit = () => {
      const b = map.getBounds()
      cb.current.onView({
        bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()],
        zoom: map.getZoom(),
      })
    }
    map.on('load', () => {
      map.addSource('trust', { type: 'geojson', data: EMPTY })
      map.addSource('hotspots', { type: 'geojson', data: EMPTY })
      map.addSource('nodes', { type: 'geojson', data: EMPTY })
      map.addLayer({
        id: 'trust-fill',
        type: 'fill',
        source: 'trust',
        paint: {
          'fill-color': stateColor,
          'fill-opacity': ['interpolate', ['linear'], ['get', 'confidence'], 0, 0.22, 1, 0.5],
        },
      })
      map.addLayer({
        id: 'trust-line',
        type: 'line',
        source: 'trust',
        paint: {
          'line-color': stateColor,
          'line-opacity': 0.35,
          'line-width': 0.5,
        },
      })
      map.addLayer({
        id: 'hotspot-fill',
        type: 'fill',
        source: 'hotspots',
        paint: {
          'fill-color': kindColor,
          'fill-opacity': ['case', ['==', ['get', 'state'], 'active'], 0.22, 0.08],
        },
      })
      for (const [id, state, dash] of [
        ['hotspot-line-active', 'active', undefined],
        ['hotspot-line-closed', 'closed', [2, 2]],
      ] as const) {
        map.addLayer({
          id,
          type: 'line',
          source: 'hotspots',
          filter: ['==', ['get', 'state'], state],
          paint: {
            'line-color': kindColor,
            'line-width': 2,
            ...(dash ? { 'line-dasharray': [...dash] } : {}),
          },
        })
      }
      map.addLayer({
        id: 'hotspot-selected',
        type: 'line',
        source: 'hotspots',
        filter: ['==', ['get', 'id'], ''],
        paint: { 'line-color': '#ffffff', 'line-width': 3.5 },
      })
      map.addLayer({
        id: 'nodes',
        type: 'circle',
        source: 'nodes',
        paint: {
          'circle-radius': 5,
          'circle-color': '#e6edf3',
          'circle-stroke-width': 2,
          'circle-stroke-color': ['case', ['get', 'sim'], '#5b8def', '#0f151b'],
        },
      })
      map.on('click', 'hotspot-fill', (e) => {
        const id = e.features?.[0]?.properties?.id as string | undefined
        if (id) cb.current.onSelect(id)
      })
      map.on('mouseenter', 'hotspot-fill', () => (map.getCanvas().style.cursor = 'pointer'))
      map.on('mouseleave', 'hotspot-fill', () => (map.getCanvas().style.cursor = ''))
      setReady(true)
      emit()
    })
    map.on('moveend', emit)
    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    ;(map.getSource('trust') as GeoJSONSource).setData(grid ?? EMPTY)
  }, [grid, ready])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || fitted.current || hotspots.length === 0) return
    const box = unionBbox(hotspots.map((h) => bboxOf(h.source_ellipse)))
    if (box)
      map.fitBounds(box as [number, number, number, number], {
        padding: 90,
        maxZoom: 12.5,
        duration: 0,
      })
    fitted.current = true
  }, [hotspots, ready])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const fc: FeatureCollection = {
      type: 'FeatureCollection',
      features: hotspots.map((h) => ({
        type: 'Feature',
        geometry: h.source_ellipse,
        properties: { id: h.id, kind: h.kind, state: h.state },
      })),
    }
    ;(map.getSource('hotspots') as GeoJSONSource).setData(fc)
  }, [hotspots, ready])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const fc: FeatureCollection = {
      type: 'FeatureCollection',
      features: nodes
        .filter((n) => n.position)
        .map((n) => ({
          type: 'Feature',
          geometry: n.position!,
          properties: { id: n.id, name: n.name, sim: n.sim },
        })),
    }
    ;(map.getSource('nodes') as GeoJSONSource).setData(fc)
  }, [nodes, ready])

  useEffect(() => {
    const map = mapRef.current
    if (map && ready) map.setFilter('hotspot-selected', ['==', ['get', 'id'], selectedId ?? ''])
  }, [selectedId, ready])

  useEffect(() => {
    if (focus && mapRef.current)
      mapRef.current.fitBounds(focus.bbox as [number, number, number, number], {
        padding: 90,
        maxZoom: 12.5,
        duration: 700,
      })
  }, [focus])

  return <div ref={box} className="map" role="application" aria-label="Map" />
}
