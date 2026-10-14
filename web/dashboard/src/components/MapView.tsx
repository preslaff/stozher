import * as maplibregl from 'maplibre-gl'
import type { GeoJSONSource, StyleSpecification } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useEffect, useRef, useState } from 'react'
import type { Bbox } from '../api'
import type { View } from '../useDashboard'
import { TIER_COLOR } from '../hazards'
import { TRUST_COLOR } from '../style'
import { bboxOf, unionBbox } from '../geo'
import type { Hotspot, NodeInfo, TrustProps } from '../types'
import type { Waypoint } from '../route'
import type { FeatureCollection, LineString, Polygon } from 'geojson'

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
const tierColor: maplibregl.ExpressionSpecification = [
  'match',
  ['get', 'tier'],
  'confirmed',
  TIER_COLOR.confirmed,
  'corroborated',
  TIER_COLOR.corroborated,
  TIER_COLOR.unverified,
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
  routeFc: FeatureCollection<LineString>
  watchedFc: FeatureCollection<LineString>
  hazardMarkers: FeatureCollection
  hazardShapes: FeatureCollection
  selectedHazardId: string | null
  onSelectHazard: (id: string | null) => void
  placing: boolean
  reportPoint: Waypoint | null
  onPlace: (p: Waypoint) => void
  waypoints: Waypoint[]
  drawing: boolean
  onAddWaypoint: (p: Waypoint) => void
  focus: { bbox: Bbox; n: number } | null
  onSelect: (id: string | null) => void
  onView: (v: View) => void
}

export function MapView({
  hotspots,
  nodes,
  grid,
  selectedId,
  routeFc,
  watchedFc,
  hazardMarkers,
  hazardShapes,
  selectedHazardId,
  onSelectHazard,
  placing,
  reportPoint,
  onPlace,
  waypoints,
  drawing,
  onAddWaypoint,
  focus,
  onSelect,
  onView,
}: Props) {
  const box = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const [ready, setReady] = useState(false)
  const cb = useRef({ onSelect, onView, onAddWaypoint, onSelectHazard, onPlace })
  const drawingRef = useRef(drawing)
  const placingRef = useRef(placing)
  useEffect(() => {
    cb.current = { onSelect, onView, onAddWaypoint, onSelectHazard, onPlace }
    drawingRef.current = drawing
    placingRef.current = placing
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
      map.addSource('watched', { type: 'geojson', data: EMPTY })
      map.addSource('route', { type: 'geojson', data: EMPTY })
      map.addSource('draft', { type: 'geojson', data: EMPTY })
      map.addSource('hazard-shapes', { type: 'geojson', data: EMPTY })
      map.addSource('hazard-markers', { type: 'geojson', data: EMPTY })
      map.addSource('report-point', { type: 'geojson', data: EMPTY })
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
        id: 'watched-line',
        type: 'line',
        source: 'watched',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': stateColor,
          'line-width': 3,
          'line-opacity': 0.9,
          'line-dasharray': [2, 1],
        },
      })
      map.addLayer({
        id: 'route-casing',
        type: 'line',
        source: 'route',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#0f151b', 'line-width': 9 },
      })
      map.addLayer({
        id: 'route-trust',
        type: 'line',
        source: 'route',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': stateColor, 'line-width': 5.5 },
      })
      map.addLayer({
        id: 'draft-line',
        type: 'line',
        source: 'draft',
        filter: ['==', ['geometry-type'], 'LineString'],
        paint: { 'line-color': '#ffffff', 'line-width': 2, 'line-dasharray': [2, 2] },
      })
      map.addLayer({
        id: 'draft-points',
        type: 'circle',
        source: 'draft',
        filter: ['==', ['geometry-type'], 'Point'],
        paint: {
          'circle-radius': 5,
          'circle-color': '#ffffff',
          'circle-stroke-width': 2,
          'circle-stroke-color': '#0f151b',
        },
      })
      map.addLayer({
        id: 'hazard-shape-fill',
        type: 'fill',
        source: 'hazard-shapes',
        filter: ['==', ['geometry-type'], 'Polygon'],
        paint: { 'fill-color': tierColor, 'fill-opacity': 0.14 },
      })
      map.addLayer({
        id: 'hazard-shape-line',
        type: 'line',
        source: 'hazard-shapes',
        paint: { 'line-color': tierColor, 'line-width': 2, 'line-dasharray': [3, 2] },
      })
      map.addLayer({
        id: 'hazard-selected',
        type: 'circle',
        source: 'hazard-markers',
        filter: ['==', ['get', 'id'], ''],
        paint: {
          'circle-radius': 15,
          'circle-color': 'rgba(0,0,0,0)',
          'circle-stroke-width': 3,
          'circle-stroke-color': '#ffffff',
        },
      })
      map.addLayer({
        id: 'hazard-markers',
        type: 'circle',
        source: 'hazard-markers',
        paint: {
          'circle-radius': ['match', ['get', 'tier'], 'confirmed', 9, 'corroborated', 8, 7],
          'circle-color': tierColor,
          'circle-opacity': ['match', ['get', 'tier'], 'unverified', 0.35, 1],
          'circle-stroke-width': 3,
          'circle-stroke-color': tierColor,
        },
      })
      map.addLayer({
        id: 'report-point',
        type: 'circle',
        source: 'report-point',
        paint: {
          'circle-radius': 8,
          'circle-color': '#ffffff',
          'circle-stroke-width': 3,
          'circle-stroke-color': '#5b8def',
        },
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
      map.on('click', (e) => {
        if (placingRef.current) cb.current.onPlace([e.lngLat.lng, e.lngLat.lat])
        else if (drawingRef.current) cb.current.onAddWaypoint([e.lngLat.lng, e.lngLat.lat])
      })
      map.on('click', 'hazard-markers', (e) => {
        if (drawingRef.current || placingRef.current) return
        const id = e.features?.[0]?.properties?.id as string | undefined
        if (id) cb.current.onSelectHazard(id)
      })
      map.on('mouseenter', 'hazard-markers', () => (map.getCanvas().style.cursor = 'pointer'))
      map.on('mouseleave', 'hazard-markers', () => (map.getCanvas().style.cursor = ''))
      map.on('click', 'hotspot-fill', (e) => {
        if (drawingRef.current) return
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
    if (!map || !ready) return
    ;(map.getSource('route') as GeoJSONSource).setData(routeFc)
  }, [routeFc, ready])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    ;(map.getSource('watched') as GeoJSONSource).setData(watchedFc)
  }, [watchedFc, ready])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const fc: FeatureCollection = {
      type: 'FeatureCollection',
      features: [
        ...(waypoints.length > 1 && !routeFc.features.length
          ? [
              {
                type: 'Feature' as const,
                geometry: { type: 'LineString' as const, coordinates: waypoints },
                properties: {},
              },
            ]
          : []),
        ...waypoints.map((w) => ({
          type: 'Feature' as const,
          geometry: { type: 'Point' as const, coordinates: w },
          properties: {},
        })),
      ],
    }
    ;(map.getSource('draft') as GeoJSONSource).setData(fc)
  }, [waypoints, routeFc, ready])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    ;(map.getSource('hazard-markers') as GeoJSONSource).setData(hazardMarkers)
    ;(map.getSource('hazard-shapes') as GeoJSONSource).setData(hazardShapes)
  }, [hazardMarkers, hazardShapes, ready])

  useEffect(() => {
    const map = mapRef.current
    if (map && ready)
      map.setFilter('hazard-selected', ['==', ['get', 'id'], selectedHazardId ?? ''])
  }, [selectedHazardId, ready])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    ;(map.getSource('report-point') as GeoJSONSource).setData(
      reportPoint
        ? {
            type: 'FeatureCollection',
            features: [
              {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: reportPoint },
                properties: {},
              },
            ],
          }
        : EMPTY,
    )
  }, [reportPoint, ready])

  useEffect(() => {
    const map = mapRef.current
    if (map) map.getCanvas().style.cursor = drawing || placing ? 'crosshair' : ''
  }, [drawing, placing])

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
