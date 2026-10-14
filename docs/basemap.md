# Offline base map

The dashboard must work when the network does not, so the base map is a file, not a service.

- **What:** a Bulgaria extract (22.3-28.7 E, 41.2-44.3 N, zoom 0-14, about 255 MB) of the Protomaps basemap, vector tiles
  built from OpenStreetMap data (ODbL, © OpenStreetMap contributors, Protomaps). The attribution shows on the map.
- **How it is read:** one PMTiles file served with HTTP range requests (`pmtiles` in the browser), styled by
  `@protomaps/basemaps` (dark flavor, Bulgarian labels where the data has them). Fonts (Noto Sans, SIL OFL) and icon
  sprites are served from the same folder, so nothing is fetched from the internet. `web/dashboard/src/basemap.ts`
  builds the style, and `basemap.test.ts` fails if the style mentions any host other than the one serving the dashboard.
- **Build it once, with internet:** `tools/basemap/fetch.sh [YYYYMMDD]` (needs `curl`, `tar`). It writes
  `data/basemap/bulgaria.pmtiles` and `web/dashboard/public/basemap/` (both git-ignored; the file is large and
  rebuilt, not versioned). `BBOX` and `MAXZOOM` can be overridden for another area.
- **Serving:** the dev server and `vite build` serve `public/basemap/` as-is (range requests work). On the field kit the
  same folder is served by whatever serves the dashboard. `VITE_BASEMAP_URL` points at a file served elsewhere.
- **Overrides:** `VITE_TILE_URL` selects an online raster layer, for development only, and wins over the file. We do not
  fall back to it silently. With no file and no URL the map shows the dark background and our layers only.
- **Limits:** outside the extract there are no tiles (zoomed out to the world the base map is empty). Detail above
  zoom 14 is the zoom 14 tiles scaled up. Data is as of the build date, so refresh it before a demo if roads matter.
