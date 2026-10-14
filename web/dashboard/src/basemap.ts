// Base map style. The default is a local PMTiles file (vector tiles, fonts and icons served next to the
// dashboard), so the map works with no internet: that is the field-kit case. A raster URL template is an
// explicit, online-only option for development. See docs/basemap.md.
import { layers, namedFlavor } from '@protomaps/basemaps'
import type { StyleSpecification } from 'maplibre-gl'

export interface BasemapConfig {
  /** Absolute URL of the PMTiles file. Empty: no offline base map. */
  pmtilesUrl: string
  /** Optional raster tile template (online only; for development). Wins over the PMTiles file when set. */
  rasterUrl?: string
  /** Absolute URL of the folder with `fonts/` and `sprites/`. */
  assetBase: string
}

const BACKGROUND = '#0f151b'

export const OSM_ATTRIBUTION = '© OpenStreetMap contributors'

export function baseStyle(cfg: BasemapConfig): StyleSpecification {
  const bg = { id: 'bg', type: 'background', paint: { 'background-color': BACKGROUND } } as const
  if (cfg.rasterUrl) {
    return {
      version: 8,
      sources: {
        base: {
          type: 'raster',
          tiles: [cfg.rasterUrl],
          tileSize: 256,
          attribution: OSM_ATTRIBUTION,
        },
      },
      layers: [
        bg,
        {
          id: 'base',
          type: 'raster',
          source: 'base',
          paint: {
            'raster-opacity': 0.65,
            'raster-saturation': -0.7,
            'raster-brightness-max': 0.65,
          },
        },
      ],
    }
  }
  if (!cfg.pmtilesUrl) return { version: 8, sources: {}, layers: [bg] }
  return {
    version: 8,
    glyphs: `${cfg.assetBase}/fonts/{fontstack}/{range}.pbf`,
    sprite: `${cfg.assetBase}/sprites/v4/dark`,
    sources: {
      protomaps: {
        type: 'vector',
        url: `pmtiles://${cfg.pmtilesUrl}`,
        attribution: `${OSM_ATTRIBUTION}, Protomaps`,
      },
    },
    layers: [bg, ...layers('protomaps', namedFlavor('dark'), { lang: 'bg' })],
  }
}
