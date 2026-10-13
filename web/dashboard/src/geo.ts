import type { Geometry, Polygon, Position } from 'geojson'
import type { Bbox } from './api'

function walk(coords: unknown, out: Position[]): void {
  if (Array.isArray(coords) && typeof coords[0] === 'number') out.push(coords as Position)
  else if (Array.isArray(coords)) coords.forEach((c) => walk(c, out))
}

/** Bounding box [minLon, minLat, maxLon, maxLat] of any GeoJSON geometry; null if empty. */
export function bboxOf(g: Geometry | Polygon | null | undefined): Bbox | null {
  if (!g || !('coordinates' in g)) return null
  const pts: Position[] = []
  walk(g.coordinates, pts)
  if (pts.length === 0) return null
  const lons = pts.map((p) => p[0])
  const lats = pts.map((p) => p[1])
  return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)]
}

export function unionBbox(boxes: (Bbox | null)[]): Bbox | null {
  const b = boxes.filter((x): x is Bbox => x !== null)
  if (b.length === 0) return null
  return [
    Math.min(...b.map((x) => x[0])),
    Math.min(...b.map((x) => x[1])),
    Math.max(...b.map((x) => x[2])),
    Math.max(...b.map((x) => x[3])),
  ]
}
